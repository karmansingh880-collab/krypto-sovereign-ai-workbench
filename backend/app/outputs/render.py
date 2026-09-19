"""
Turn model-written text into real files.

The model only ever writes simple markdown-like text ("# Title", "## Heading",
"- bullet", plain paragraphs, "| a | b |" tables). This module parses that into
blocks and renders the same blocks as a Word document or a PDF, so both formats
always match.
"""

from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pymupdf
from docx import Document
from docx.shared import Inches, Pt

Block = Dict[str, Any]

_BULLET_RE = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s+(.*\S)\s*$")
_HEADING_RE = re.compile(r"^\s*(#{1,4})\s+(.*\S)\s*$")
_PREFACE_RE = re.compile(r"^\s*(?:here(?:'s| is| are)|sure|certainly|of course|below is)\b.*:\s*$", re.I)
_BOLD_LINE_RE = re.compile(r"^\s*\*\*(.{2,80}?)\*\*:?\s*$")


def clean_model_text(text: str) -> str:
    """Drop chatty prefaces and code fences the model adds around the content."""
    text = re.sub(r"^```[a-z]*\s*|\s*```\s*$", "", text.strip(), flags=re.I)
    lines = text.splitlines()
    while lines and (not lines[0].strip() or _PREFACE_RE.match(lines[0])):
        lines.pop(0)
    return "\n".join(lines).strip()


_COMMON_HEADINGS = {
    "aim", "objective", "objectives", "procedure", "result", "results", "conclusion", "summary",
    "introduction", "overview", "theory", "output", "requirements", "learning outcomes", "key points",
}


def _bullets_to_blocks(items: List[str]) -> List[Block]:
    """Small models write section names as bullets. Promote a short, unpunctuated bullet to a
    heading only when the next bullet is clearly a sentence -- a plain list stays a list."""
    out: List[Block] = []
    current: List[str] = []
    for index, item in enumerate(items):
        text = item.strip()
        nxt = items[index + 1].strip() if index + 1 < len(items) else ""
        looks_short = (
            len(text.split()) <= 5 and not re.search(r"[.:;,!?]$", text) and ":" not in text and text[:1].isupper()
        )
        next_is_sentence = bool(nxt) and (nxt.endswith(".") or len(nxt.split()) >= len(text.split()) + 3)
        if text.lower() in _COMMON_HEADINGS or (looks_short and next_is_sentence):
            if current:
                out.append({"type": "ul", "items": current})
                current = []
            out.append({"type": "h", "text": text})
        else:
            current.append(item)
    if current:
        out.append({"type": "ul", "items": current})
    return out


def parse_blocks(text: str, default_title: str = "Document") -> List[Block]:
    blocks: List[Block] = []
    paragraph: List[str] = []
    bullets: List[str] = []
    table_rows: List[List[str]] = []

    def flush() -> None:
        nonlocal paragraph, bullets, table_rows
        if paragraph:
            blocks.append({"type": "p", "text": " ".join(paragraph)})
        if bullets:
            blocks.extend(_bullets_to_blocks(bullets))
        if table_rows:
            blocks.append({"type": "table", "headers": table_rows[0], "rows": table_rows[1:]})
        paragraph, bullets, table_rows = [], [], []

    in_code = False
    code_lines: List[str] = []

    for raw in text.splitlines():
        line = raw.rstrip()

        if line.strip().startswith("```"):
            if in_code:
                blocks.append({"type": "code", "text": "\n".join(code_lines)})
                code_lines, in_code = [], False
            else:
                flush()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if "```" in line:
            line = re.sub(r"```\w*", "", line)  # a fence glued into a sentence: keep the text, drop the marks

        if not line.strip():
            flush()
            continue

        # Small models often write headings as bullets ("- # Title"): treat those as headings.
        as_bullet = _BULLET_RE.match(line)
        if as_bullet and as_bullet.group(1).lstrip().startswith("#"):
            line = as_bullet.group(1).lstrip()

        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # the |---|---| separator row
            if paragraph or bullets:
                flush()
            table_rows.append(cells)
            continue

        heading = _HEADING_RE.match(line)
        bold_line = _BOLD_LINE_RE.match(line)
        bullet = _BULLET_RE.match(line)
        if heading or bold_line:
            flush()
            if heading:
                level, title = len(heading.group(1)), heading.group(2)
            else:
                level, title = 2, bold_line.group(1)
            has_title = any(b["type"] == "title" for b in blocks)
            blocks.append({"type": "h" if (has_title or level > 1) else "title", "text": title.strip("* ")})
        elif bullet:
            if paragraph or table_rows:
                flush()
            bullets.append(bullet.group(1))
        else:
            if bullets or table_rows:
                flush()
            paragraph.append(line.strip())
    if in_code and code_lines:
        blocks.append({"type": "code", "text": "\n".join(code_lines)})
    flush()

    if not any(b["type"] == "title" for b in blocks):
        blocks.insert(0, {"type": "title", "text": default_title})
    return blocks


def _runs(text: str) -> List[tuple]:
    """Split '**bold** and plain' into (text, is_bold) runs."""
    parts = re.split(r"\*\*(.+?)\*\*", text)
    return [(part, i % 2 == 1) for i, part in enumerate(parts) if part]


def _plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def render_docx(blocks: List[Block], path: Path) -> str:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)

    def add_runs(paragraph, text: str) -> None:
        for chunk, bold in _runs(text):
            paragraph.add_run(chunk).bold = bold

    for block in blocks:
        kind = block["type"]
        if kind == "title":
            doc.add_heading(_plain(block["text"]), 0)
        elif kind == "h":
            doc.add_heading(_plain(block["text"]), 1)
        elif kind == "p":
            add_runs(doc.add_paragraph(), block["text"])
        elif kind == "ul":
            for item in block["items"]:
                add_runs(doc.add_paragraph(style="List Bullet"), item)
        elif kind == "code":
            run = doc.add_paragraph().add_run(block["text"])
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif kind == "table":
            headers = block["headers"]
            table = doc.add_table(rows=1, cols=len(headers))
            table.style = "Light Grid Accent 1"
            for cell, text in zip(table.rows[0].cells, headers):
                cell.text = ""
                cell.paragraphs[0].add_run(_plain(text)).bold = True
            for row in block["rows"]:
                cells = table.add_row().cells
                for i, cell in enumerate(cells):
                    cell.text = _plain(row[i]) if i < len(row) else ""
            doc.add_paragraph()
        elif kind == "image":
            doc.add_picture(block["path"], width=Inches(min(6.0, block.get("width_in", 6.0))))
            if block.get("caption"):
                caption = doc.add_paragraph()
                run = caption.add_run(block["caption"])
                run.italic = True
                run.font.size = Pt(9)

    footer = doc.sections[0].footer.paragraphs[0]
    footer.text = f"Generated by Krypto on {date.today().isoformat()}"
    doc.save(str(path))
    return str(path)


_PDF_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 20pt; margin: 0 0 10pt 0; }
h2 { font-size: 14pt; margin: 14pt 0 4pt 0; }
p { margin: 0 0 7pt 0; }
ul { margin: 0 0 7pt 0; }
li { margin: 0 0 3pt 0; }
table { margin: 4pt 0 10pt 0; }
th { font-weight: bold; background-color: #e8eef7; padding: 3pt; border: 1px solid #9aa7b8; }
td { padding: 3pt; border: 1px solid #9aa7b8; }
.code { font-family: monospace; font-size: 8.5pt; background-color: #f1f3f6; padding: 4pt; margin: 0 0 8pt 0; }
.cap { font-size: 8.5pt; font-style: italic; margin: 2pt 0 10pt 0; }
.foot { font-size: 8pt; color: #666666; margin-top: 14pt; }
"""


def _pdf_inline(text: str) -> str:
    out = []
    for chunk, bold in _runs(text):
        safe = html.escape(chunk)
        out.append(f"<b>{safe}</b>" if bold else safe)
    return "".join(out)


def render_pdf(blocks: List[Block], path: Path) -> str:
    archive = pymupdf.Archive()
    parts: List[str] = []
    for index, block in enumerate(blocks):
        kind = block["type"]
        if kind == "title":
            parts.append(f"<h1>{_pdf_inline(block['text'])}</h1>")
        elif kind == "h":
            parts.append(f"<h2>{_pdf_inline(block['text'])}</h2>")
        elif kind == "p":
            parts.append(f"<p>{_pdf_inline(block['text'])}</p>")
        elif kind == "ul":
            parts.append("<ul>" + "".join(f"<li>{_pdf_inline(i)}</li>" for i in block["items"]) + "</ul>")
        elif kind == "code":
            safe = html.escape(block["text"]).replace("  ", "&nbsp;&nbsp;").replace("\n", "<br>")
            parts.append(f'<p class="code">{safe}</p>')
        elif kind == "table":
            head = "".join(f"<th>{_pdf_inline(h)}</th>" for h in block["headers"])
            body = "".join(
                "<tr>" + "".join(f"<td>{_pdf_inline(c)}</td>" for c in row) + "</tr>" for row in block["rows"]
            )
            parts.append(f"<table><tr>{head}</tr>{body}</table>")
        elif kind == "image":
            name = f"img{index}.png"
            archive.add(Path(block["path"]).read_bytes(), name)
            width_pt = int(min(6.0, block.get("width_in", 6.0)) * 72 * 0.95)
            parts.append(f'<p><img src="{name}" width="{width_pt}"></p>')
            if block.get("caption"):
                parts.append(f'<p class="cap">{html.escape(block["caption"])}</p>')
    parts.append(f'<p class="foot">Generated by Krypto on {date.today().isoformat()}</p>')

    story = pymupdf.Story(html="<body>" + "".join(parts) + "</body>", user_css=_PDF_CSS, archive=archive)
    writer = pymupdf.DocumentWriter(str(path))
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (54, 54, -54, -54)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return str(path)
