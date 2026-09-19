"""
Runs that end in files: a Word document, a PDF, pictures pulled from the attached
PDF, or a chart/infographic drawn from data.

Same result shape as the agent's run_agent_task -- {status, steps, outputs,
final_message} -- so the routes and the UI treat both alike. The model only
writes text (or 'label: number' lines); everything that makes a file is code.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app import live
from backend.app.agent.doc_context import long_document_notes, needs_whole_document_notes, select_context
from backend.app.llm import chat as llm_chat
from backend.app.agent.graph import _parse_readings_from_ocr_text
from backend.app.outputs import charts, render
from backend.app.outputs.intent import Intent
from backend.app.outputs.pdf_images import extract_docx_images, extract_images
from backend.app.router.router import demo_model, resolve_model_tag
from backend.app.tools.ocr import extract_text

OUTPUT_ROOT = Path.home() / ".krypto_output"
GENERAL_MODEL_TAG = "qwen3:14b"
NUM_CTX = 8192
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
MAX_EMBEDDED_IMAGES = 4


def _ask(prompt: str, max_tokens: int = 900, stream: bool = False) -> str:
    """One model call. `max_tokens` keeps a slow CPU from writing for ten minutes; `stream` shows the
    text live to a browser watching the run."""
    text = llm_chat(
        resolve_model_tag(GENERAL_MODEL_TAG),
        prompt,
        options={"num_ctx": NUM_CTX} if demo_model() else None,
        max_tokens=max_tokens,
        stream_to_ui=stream,
    ).strip()
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _table_chart(goal: str, doc_text: str, out_dir: Path) -> Optional[Tuple[str, str, Dict[str, List[float]], List[str]]]:
    """Chart a readings table (location / previous / current / years) exactly, without asking the model.

    Inspection reports come out of OCR one cell per line, which a small model
    pairs up unreliably; the same parser the corrosion workflow uses reads them
    reliably. Returns (path, title, series, labels), or None if the text has no such table.
    """
    readings = _parse_readings_from_ocr_text(doc_text)
    if len(readings) < 2:
        return None
    labels = [r["location"] for r in readings]
    wants_prev = re.search(r"\b(previous|earlier|before|old|initial)\b", goal, re.I)
    wants_cur = re.search(r"\b(current|latest|now|recent|present)\b", goal, re.I)
    compare = re.search(r"\b(compar\w*|versus|vs|both|side by side)\b", goal, re.I)
    wants_rate = re.search(r"\b(corrosion rate|rate of corrosion|rate)\b", goal, re.I)

    if wants_rate:
        title = "Corrosion rate by location (mm/year)"
        series = {"Corrosion rate (mm/yr)": [round((r["previous_thickness"] - r["current_thickness"]) / r["years"], 3)
                                             for r in readings]}
    elif (wants_prev and wants_cur) or compare:
        title = "Wall thickness by location: previous vs current (mm)"
        series = {"Previous (mm)": [r["previous_thickness"] for r in readings],
                  "Current (mm)": [r["current_thickness"] for r in readings]}
    elif wants_prev:
        title = "Previous wall thickness by location (mm)"
        series = {"Previous (mm)": [r["previous_thickness"] for r in readings]}
    else:
        title = "Current wall thickness by location (mm)"
        series = {"Current (mm)": [r["current_thickness"] for r in readings]}

    path = out_dir / f"{_slug(title)}.png"
    if len(series) > 1:
        charts.grouped_bar_chart(title, labels, series, path)
    else:
        charts.chart_from_data(title, "bar", labels, next(iter(series.values())), path)
    return str(path), title, series, labels


def _slug(text: str, fallback: str = "krypto") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]
    return slug or fallback


def _step(steps: list, title: str, tool: Optional[str] = None, args: Any = None,
          response: str = "", error: Optional[str] = None) -> None:
    steps.append({"step": title, "tool_used": tool, "tool_args": args, "response": response, "error": error})


def _content_prompt(goal: str, context: str) -> str:
    """The writing prompt; a follow-up also carries the earlier question and answer."""
    earlier = live.conversation()
    request = f"{goal}\n\nEarlier in this conversation:\n{earlier}" if earlier else goal
    return _content_prompt_base(request, context)


def _content_prompt_base(goal: str, context: str) -> str:
    source = context if context else "(no source document was attached)"
    return (
        "Write the document the user asked for. Output ONLY the document itself, in this exact format:\n"
        "- first line: '# ' followed by a short title (on its own line, not a bullet)\n"
        "- then sections: '## Heading' on its own line, short paragraphs, and '- ' bullet lines\n"
        "- never put '#' inside a bullet line; no introduction such as 'Here is...'; no closing remarks\n"
        "Be concise: about 250 words, unless the request clearly asks for the full text. "
        "A summary must be shorter than the source, in your own words, not a copy of it.\n"
        "Use only facts from the SOURCE TEXT when it is provided; never invent details. "
        "If there is no source text, write a short, accurate document from general knowledge.\n\n"
        f"REQUEST: {goal}\n\nSOURCE TEXT:\n{source}\n"
    )


def _chart_prompt(goal: str, context: str) -> str:
    source = context if context else "(no source document was attached)"
    return (
        "Find the data to plot for the request. Answer in exactly this format and nothing else:\n"
        "TITLE: <short title>\nTYPE: bar   (or line, or pie)\n"
        "then one line per data point, written as  name: number  using the REAL name from the text.\n"
        "Example of a valid answer:\nTITLE: Quarterly sales\nTYPE: bar\nQ1: 120\nQ2: 150\nQ3: 90\n\n"
        "Never write the word 'label' as a name. "
        "Use only numbers that appear in the SOURCE TEXT or in the REQUEST itself. "
        "If there are no numbers to plot, answer with the single word NONE.\n\n"
        f"REQUEST: {goal}\n\nSOURCE TEXT:\n{source}\n"
    )


def _infographic_prompt(goal: str, context: str) -> str:
    source = context if context else "(no source document was attached)"
    return (
        "Write the text for a one-page infographic. First line: a short title (max 8 words). "
        "Then 4 to 6 lines, each starting with '- ', each a key point of at most 14 words. "
        "Use only facts from the SOURCE TEXT when provided. No other text.\n\n"
        f"REQUEST: {goal}\n\nSOURCE TEXT:\n{source}\n"
    )


def _announce(intent: Intent, file_paths: List[str], label: Any) -> None:
    """Tell a watching browser which steps to expect, in order."""
    if not live.watching():
        return
    items: List[Tuple[str, str]] = []
    if intent.docx or intent.pdf or intent.image == "generate":
        items += [(f"read:{i}", f"Reading {label(i)}") for i in range(len(file_paths))]
    if intent.image == "extract":
        items.append(("images", "Extracting the pictures"))
    if intent.docx or intent.pdf:
        items.append(("write", "Writing the document"))
    if intent.image == "generate":
        items.append(("chart", "Drawing the picture"))
    if intent.docx:
        items.append(("docx", "Building the Word file"))
    if intent.pdf:
        items.append(("pdf", "Building the PDF"))
    live.plan(items)


def _run_sync(goal: str, file_paths: List[str], intent: Intent, names: List[str]) -> Dict[str, Any]:
    steps: List[dict] = []
    notes: List[str] = []
    doc_outputs: List[str] = []
    image_outputs: List[str] = []
    embed: List[Tuple[str, str]] = []  # (image path, caption) to place inside Word/PDF files
    out_dir = OUTPUT_ROOT / "generated" / uuid.uuid4().hex[:10]
    out_dir.mkdir(parents=True, exist_ok=True)

    def label(index: int) -> str:
        return names[index] if index < len(names) else Path(file_paths[index]).name

    # 1. read the attached file(s) -- only when the text is actually needed
    doc_text = ""
    text_read = False

    def read_files() -> None:
        nonlocal doc_text, text_read
        if text_read:
            return
        text_read = True
        for i, path in enumerate(file_paths):
            slow = Path(path).suffix.lower() in IMAGE_SUFFIXES
            live.start(f"read:{i}", f"Reading {label(i)}", "Recognizing the text with OCR; this can take a minute" if slow else "")
            try:
                text = extract_text(path)
                # with several files, mark where each one starts
                doc_text += ("\n" if doc_text else "") + (f"=== {label(i)} ===\n{text}" if len(file_paths) > 1 else text)
                _step(steps, f"[SYSTEM] read attached file {label(i)}", "extract_text", {"file_path": path}, text)
                live.done(f"read:{i}", f"{len(text):,} characters read")
            except Exception as exc:
                _step(steps, f"[SYSTEM] read attached file {label(i)}", "extract_text", {"file_path": path}, error=str(exc))
                live.fail(f"read:{i}", str(exc))

    mode = intent.image
    _announce(intent, file_paths, label)
    if intent.docx or intent.pdf or mode == "generate":
        read_files()

    # 2. pictures that already exist in the attached file
    if mode == "extract":
        live.start("images", "Extracting the pictures", "Skipping repeated header logos and icons")
        found_any = False
        for i, path in enumerate(file_paths):
            suffix = Path(path).suffix.lower()
            try:
                if suffix in (".pdf", ".docx"):
                    dest = out_dir / ("figures" if i == 0 else f"figures{i + 1}")
                    found = extract_images(path, dest, goal) if suffix == ".pdf" else extract_docx_images(path, dest)
                    if len(file_paths) > 1:   # several files: keep their pictures apart by name
                        for f in found:
                            renamed = Path(f.path).with_name(f"{_slug(Path(label(i)).stem)}_{Path(f.path).name}")
                            Path(f.path).rename(renamed)
                            f.path = str(renamed)
                    for f in found:
                        image_outputs.append(f.path)
                        where = f"Page {f.page}" if f.page else label(i)
                        embed.append((f.path, where + (f": {f.caption}" if f.caption else "")))
                    if found:
                        found_any = True
                        listing = "\n".join(
                            (f"Page {f.page}" if f.page else f"Image {n}") + f": {f.width}x{f.height}px"
                            + (f" - {f.caption}" if f.caption else "")
                            for n, f in enumerate(found, 1)
                        )
                        _step(steps, f"[SYSTEM] extract images from {label(i)}", "extract_pdf_images",
                              {"file_path": path}, listing)
                        pages = sorted({f.page for f in found if f.page})
                        if pages:
                            notes.append(
                                f"Found {len(found)} image(s) in {label(i)} (page{'s' if len(pages) > 1 else ''} "
                                f"{', '.join(map(str, pages))}). Header logos and icons were skipped."
                            )
                        else:
                            notes.append(f"Found {len(found)} image(s) in {label(i)}.")
                elif suffix in IMAGE_SUFFIXES:
                    copy = out_dir / f"{_slug(Path(label(i)).stem)}{suffix}"
                    shutil.copy(path, copy)
                    image_outputs.append(str(copy))
                    embed.append((str(copy), label(i)))
                    found_any = True
                    _step(steps, f"[SYSTEM] return the attached image {label(i)}", "extract_pdf_images",
                          {"file_path": path}, str(copy))
                    notes.append(f"Returned the attached image {label(i)}.")
            except Exception as exc:
                _step(steps, f"[SYSTEM] extract images from {label(i)}", "extract_pdf_images",
                      {"file_path": path}, error=str(exc))
        live.done("images", f"{len(image_outputs)} picture(s) found" if found_any else "none found")
        if not found_any:
            notes.append("No pictures were found in the attached file, so I drew one from its data instead.")
            mode = "generate"
            read_files()

    # 3. the written content, for Word / PDF
    content_text = ""
    if intent.docx or intent.pdf:
        live.start("write", "Writing the document", "The local model is writing the text")
        try:
            context = ""
            if doc_text and needs_whole_document_notes(doc_text, goal):
                # A summary of a LONG document: take notes section by section so the whole
                # document is covered, then write from the notes.
                notes_text, covered, total = long_document_notes(
                    doc_text, _ask,
                    progress=lambda n, of: live.update("write", f"Reading section {n} of {of} of the long document", n, of),
                )
                live.update("write", "The local model is writing the text")
                if notes_text:
                    context = f"(Notes taken from {covered} of {total} sections of a long document)\n{notes_text}"
                    _step(steps, f"Read the long document in {covered} of {total} sections", None, None, notes_text)
                    if covered < total:
                        notes.append(f"The document is long: I summarized {covered} evenly spread sections out of {total}.")
            if not context and doc_text:
                context = select_context(doc_text, goal)
            content_text = render.clean_model_text(_ask(_content_prompt(goal, context), stream=True))
            _step(steps, "Write the document content", None, None, content_text)
            live.done("write", f"{len(content_text.split()):,} words written")
        except Exception as exc:
            _step(steps, "Write the document content", None, None, error=str(exc))
            live.fail("write", str(exc))

    # 4. a picture drawn from data (or an infographic when there is nothing to plot)
    if mode == "generate":
        live.start("chart", "Drawing the picture", "Finding the numbers to plot")
        try:
            context = select_context(doc_text, goal) if doc_text else ""
            table = _table_chart(goal, doc_text, out_dir) if doc_text else None
            # Only chart numbers that exist: in the document, or typed in the instruction. With neither, the model
            # would be inventing data, so go straight to the infographic (below) instead.
            has_numbers = bool(doc_text) or len(re.findall(r"\d+(?:\.\d+)?", goal)) >= 2
            raw = "" if (table or not has_numbers) else _ask(_chart_prompt(goal, context), max_tokens=250)
            data = None if table else charts.parse_chart_data(raw)
            if table:
                path, title, series, labels = table
                _step(steps, f"[SYSTEM] draw a bar chart from the report's table: {title}", "generate_chart",
                      {"labels": labels, "series": series}, path)
                notes.append(f"Drew a bar chart from the readings table in the report ({len(labels)} locations).")
            elif data:
                title, kind, labels, values = data
                path = charts.chart_from_data(title, kind, labels, values, out_dir / f"{_slug(title)}.png")
                _step(steps, f"[SYSTEM] draw a {kind} chart: {title}", "generate_chart",
                      {"type": kind, "labels": labels, "values": values}, path)
                source = "the document" if doc_text else "the numbers in your instruction"
                notes.append(f"Drew a {kind} chart of {len(values)} data points from {source}.")
            else:
                raw = render.clean_model_text(_ask(_infographic_prompt(goal, context), max_tokens=250))
                lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                title = re.sub(r"^[#*\s]+|[*\s:]+$", "", lines[0]) if lines else "Key points"
                points = []
                for ln in lines[1:]:
                    if not re.match(r"^\s*[-*•\d]", ln):
                        continue
                    point = re.sub(r"^[-*•\d.)\s]+", "", ln).strip(" '\"")
                    # drop filler the model adds: sub-headings ("Key points:"), fragments, repeats
                    if len(point) >= 14 and not point.endswith(":") and point not in points:
                        points.append(point)
                path = charts.infographic(title, points, out_dir / f"{_slug(title)}.png")
                _step(steps, f"[SYSTEM] draw an infographic: {title}", "generate_infographic",
                      {"title": title, "points": points}, path)
                notes.append(
                    "There were no numbers to chart, so I made an infographic of the key points. "
                    "Photo-realistic AI images are not available in this offline setup."
                    + ("" if doc_text else " No document was attached, so the points come from the model's general knowledge: please check them.")
                )
            image_outputs.append(path)
            embed.append((path, title))
            live.done("chart", title)
        except Exception as exc:
            _step(steps, "[SYSTEM] draw an image", "generate_chart", None, error=str(exc))
            notes.append(f"Could not create the image: {exc}")
            live.fail("chart", str(exc))

    # 5. the Word / PDF files
    if content_text and (intent.docx or intent.pdf):
        try:
            blocks = render.parse_blocks(content_text, default_title="Krypto document")
            title = next(b["text"] for b in blocks if b["type"] == "title")
            for path, caption in embed[:MAX_EMBEDDED_IMAGES]:
                blocks.append({"type": "image", "path": path, "caption": caption, "width_in": 5.5})
            base = _slug(title)
            if intent.docx:
                with live.stage("docx", "Building the Word file"):
                    path = render.render_docx(blocks, out_dir / f"{base}.docx")
                doc_outputs.append(path)
                _step(steps, "[SYSTEM] create the Word document", "generate_docx", {"title": title}, path)
            if intent.pdf:
                with live.stage("pdf", "Building the PDF"):
                    path = render.render_pdf(blocks, out_dir / f"{base}.pdf")
                doc_outputs.append(path)
                _step(steps, "[SYSTEM] create the PDF", "generate_pdf", {"title": title}, path)
        except Exception as exc:
            _step(steps, "[SYSTEM] create the document files", "generate_docx", None, error=str(exc))
            notes.append(f"Could not create the document file: {exc}")
    elif intent.docx or intent.pdf:
        notes.append("The model did not return any content, so no document was created.")

    # Many pictures: also offer them in one download.
    if intent.image == "extract" and len(image_outputs) > 4:
        try:
            zip_path = out_dir / "all-images.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as archive:
                for image in image_outputs:
                    archive.write(image, Path(image).name)
            doc_outputs.append(str(zip_path))
            _step(steps, f"[SYSTEM] pack {len(image_outputs)} images into a zip", "generate_zip", None, str(zip_path))
        except Exception as exc:
            _step(steps, "[SYSTEM] pack the images into a zip", "generate_zip", None, error=str(exc))

    outputs = doc_outputs + image_outputs

    if outputs:
        status = "success"
        final_message = ("\n".join(notes) or f"Created {len(outputs)} file(s).")[:500]
    else:
        status = "failed"
        final_message = "No file could be created. " + " ".join(notes)
    return {"status": status, "steps": steps, "outputs": outputs, "final_message": final_message.strip()}


async def run_content_task(goal: str, file_paths: List[str], intent: Intent,
                           names: Optional[List[str]] = None) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(_run_sync, goal, file_paths, intent, names or [])
    except Exception as exc:
        return {"status": "failed", "steps": [], "outputs": [], "final_message": f"Could not create the files: {exc}"}


# ---------------------------------------------------------------------------
# Optional extras for the corrosion workflow (only when the user asks for them)
# ---------------------------------------------------------------------------

def _corrosion_extras_sync(result: Dict[str, Any], intent: Intent) -> Dict[str, Any]:
    note_step = next(
        (s for s in reversed(result["steps"])
         if s.get("tool_used") == "generate_approval_note" and not s.get("error")),
        None,
    )
    data = ((note_step or {}).get("tool_args") or {}).get("data")
    if not data or not isinstance(data.get("corrosion_table"), list):
        return result

    out_dir = OUTPUT_ROOT / "generated" / uuid.uuid4().hex[:10]
    out_dir.mkdir(parents=True, exist_ok=True)
    steps, outputs = list(result["steps"]), list(result["outputs"])
    chart_path: Optional[str] = None

    if intent.image is not None:
        live.start("extras-chart", "Drawing the corrosion chart")
        try:
            chart_path = charts.corrosion_chart(data["corrosion_table"], data.get("required_thickness"),
                                                out_dir / "corrosion-assessment.png")
            outputs.append(chart_path)
            _step(steps, "[SYSTEM] draw the corrosion chart", "generate_chart", {"type": "corrosion"}, chart_path)
            live.done("extras-chart")
        except Exception as exc:
            _step(steps, "[SYSTEM] draw the corrosion chart", "generate_chart", None, error=str(exc))
            live.fail("extras-chart", str(exc))

    if intent.pdf:
        live.start("extras-pdf", "Building the approval note as a PDF")
        try:
            table = data["corrosion_table"]
            fmt = lambda v: "-" if v is None else f"{v:g}"  # noqa: E731
            blocks: List[dict] = [
                {"type": "title", "text": str(data.get("title", "Approval Note"))},
                {"type": "p", "text": f"Date: {data.get('date', '')}    Prepared by: {data.get('prepared_by', '')}"},
                {"type": "h", "text": "Findings summary"},
                {"type": "p", "text": str(data.get("findings_summary", ""))},
                {"type": "h", "text": "Corrosion assessment"},
                {"type": "table",
                 "headers": ["Location", "Previous (mm)", "Current (mm)", "Rate (mm/yr)", "Life left (yr)", "Status"],
                 "rows": [[str(r.get("location", "")), fmt(r.get("previous_thickness")), fmt(r.get("current_thickness")),
                           fmt(r.get("corrosion_rate")), fmt(r.get("remaining_life")), str(r.get("status", ""))]
                          for r in table]},
            ]
            if chart_path:
                blocks.append({"type": "image", "path": chart_path, "caption": "Corrosion rate and remaining life per location", "width_in": 6})
            citations = data.get("sop_citations") or []
            if citations:
                blocks.append({"type": "h", "text": "Referenced SOP"})
                blocks.append({"type": "ul", "items": [
                    f"{c.get('doc_name', '')}, page {c.get('page', '')}: {' '.join(str(c.get('text', '')).split())[:220]}"
                    for c in citations]})
            blocks.append({"type": "h", "text": "Recommendation"})
            blocks.append({"type": "p", "text": str(data.get("recommendation", ""))})
            path = render.render_pdf(blocks, out_dir / "approval-note.pdf")
            outputs.insert(len(result["outputs"]), path)
            _step(steps, "[SYSTEM] create the approval note as PDF", "generate_pdf", {"title": data.get("title")}, path)
            live.done("extras-pdf")
        except Exception as exc:
            _step(steps, "[SYSTEM] create the approval note as PDF", "generate_pdf", None, error=str(exc))
            live.fail("extras-pdf", str(exc))

    return {**result, "steps": steps, "outputs": outputs}


async def add_corrosion_extras(result: Dict[str, Any], intent: Intent) -> Dict[str, Any]:
    if result.get("status") != "success" or not (intent.pdf or intent.image):
        return result
    try:
        return await asyncio.to_thread(_corrosion_extras_sync, result, intent)
    except Exception:
        return result  # the original corrosion result is still valid without the extras
