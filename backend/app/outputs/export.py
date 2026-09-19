"""
Turn a finished run's answer into a Word file, a PDF or an image, on demand.

No model is involved (the answer already exists), so this takes a moment. Used by
"POST /tasks/{id}/export" -- the "download this answer as ..." buttons.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.outputs import charts, render
from backend.app.outputs.intent import Intent

OUTPUT_ROOT = Path.home() / ".krypto_output"
FORMATS = ("docx", "pdf", "image")


class ExportError(ValueError):
    """The run cannot be exported in this format (the message is shown to the user)."""


def answer_of(steps: List[Dict[str, Any]]) -> str:
    """The run's last real written answer (steps run by the system do not count)."""
    answered = [
        s for s in steps
        if not s.get("error") and s.get("response") and not str(s.get("step", "")).startswith("[SYSTEM]")
    ]
    return str(answered[-1]["response"]) if answered else ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "answer"


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _points_from(answer: str) -> List[str]:
    """Key points for an image card: the answer's bullet lines, else its sentences."""
    lines = [ln.strip() for ln in answer.splitlines() if ln.strip()]
    bullets = [re.sub(r"^[-*•–\d.)\s]+", "", ln).strip() for ln in lines if re.match(r"^\s*[-*•–]|^\s*\d+[.)]", ln)]
    if len(bullets) < 2:
        bullets = [s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join(lines)) if len(s.strip()) > 12]
    seen: List[str] = []
    for point in bullets:
        point = point.strip(" '\"*")
        if len(point) >= 8 and not point.endswith(":") and point not in seen:
            seen.append(point)
    return seen[:6]


def export_answer(
    steps: List[Dict[str, Any]],
    goal: str,
    assessment: Optional[Dict[str, Any]],
    fmt: str,
    existing_outputs: List[str],
) -> str:
    """Create the file and return its path. Raises ExportError if it cannot be made."""
    if fmt not in FORMATS:
        raise ExportError(f"Unknown format “{fmt}”. Choose one of: Word, PDF, image.")

    out_dir = OUTPUT_ROOT / "generated" / uuid.uuid4().hex[:10]
    out_dir.mkdir(parents=True, exist_ok=True)

    if assessment:  # the corrosion workflow: its own note and chart
        if fmt == "docx":
            note = next((p for p in existing_outputs if Path(p).name.startswith("approval_note_")), None)
            if note:
                return note
            raise ExportError("The approval note (.docx) was not created for this run.")
        from backend.app.outputs.pipeline import _corrosion_extras_sync

        wanted = Intent(pdf=(fmt == "pdf"), image=("generate" if fmt == "image" else None), corrosion=True)
        made = _corrosion_extras_sync({"status": "success", "steps": steps, "outputs": []}, wanted)["outputs"]
        if not made:
            raise ExportError("There is no corrosion result to export for this run.")
        return made[0]

    answer = answer_of(steps)
    if not answer.strip():
        raise ExportError("This run has no written answer to export.")

    cleaned = render.clean_model_text(answer) or answer
    fallback_title = _short(goal, 60) or "Answer"

    if fmt in ("docx", "pdf"):
        blocks = render.parse_blocks(cleaned, default_title=fallback_title)
        title_index = next((i for i, b in enumerate(blocks) if b["type"] == "title"), 0)
        title = blocks[title_index]["text"]
        blocks.insert(title_index + 1, {"type": "p", "text": f"**Question:** {_short(goal, 400)}"})
        path = out_dir / f"{_slug(title)}.{fmt}"
        return render.render_docx(blocks, path) if fmt == "docx" else render.render_pdf(blocks, path)

    # image: a chart when the answer holds numbers to plot, else a card of its key points
    data = charts.parse_chart_data(cleaned)
    if data:
        title, kind, labels, values = data
        return charts.chart_from_data(title, kind, labels, values, out_dir / f"{_slug(title)}.png")
    points = _points_from(cleaned)
    if not points:
        raise ExportError("The answer is too short to turn into an image.")
    title = _short(goal, 70) or "Answer"
    return charts.infographic(title, points, out_dir / f"{_slug(title)}.png")
