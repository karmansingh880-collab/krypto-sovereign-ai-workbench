"""
Pull the figures (graphs, screenshots, photos) out of a PDF as PNG files.

Each picture is rendered from the page area it occupies, so what you get is what
you see in the PDF (masks and overlays included). Repeated header/footer logos
and tiny icons are skipped.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pymupdf

MIN_WIDTH_PX, MIN_HEIGHT_PX = 160, 90       # smaller than this is an icon, not a figure
HEADER_ZONE = 0.18                          # a picture wholly inside the top 18% of a page is a banner
MAX_IMAGES = 60
MIN_DPI, MAX_DPI = 96, 220

_CAPTION_RE = re.compile(r"(?i)\b(fig(?:ure)?\.?|graph|chart|output|screenshot|table|plot|diagram)\b")


@dataclass
class FoundImage:
    path: str
    page: int
    width: int
    height: int
    caption: str


def _caption_near(page: "pymupdf.Page", bbox: "pymupdf.Rect") -> str:
    """Text just below the picture (or, failing that, just above it)."""
    below, above = [], []
    for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
        text = " ".join(text.split())
        if not text:
            continue
        horizontally_related = x1 > bbox.x0 - 20 and x0 < bbox.x1 + 20
        if horizontally_related and bbox.y1 - 4 <= y0 <= bbox.y1 + 60:
            below.append((y0, text))
        elif horizontally_related and bbox.y0 - 60 <= y1 <= bbox.y0 + 4:
            above.append((-y1, text))
    for group in (below, above):
        if group:
            return sorted(group)[0][1][:140]
    return ""


def extract_images(pdf_path: str, out_dir: Path, instruction: str = "") -> List[FoundImage]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    try:
        placements = []  # (page_no, bbox, width, height, xref)
        for page_no, page in enumerate(doc, 1):
            for info in page.get_image_info(xrefs=True):
                bbox = pymupdf.Rect(info["bbox"])
                width, height = info.get("width", 0), info.get("height", 0)
                if width < MIN_WIDTH_PX or height < MIN_HEIGHT_PX or bbox.is_empty:
                    continue
                if bbox.y1 < page.rect.height * HEADER_ZONE:
                    continue  # banner / logo strip at the top of the page
                placements.append((page_no, bbox, width, height, info.get("xref", 0)))

        # The same picture (or one of identical size) on several pages is a
        # running header/footer or watermark, not content.
        seen_on_pages = Counter()
        for page_no, _, w, h, xref in {(p, None, w, h, x) for p, _, w, h, x in placements}:
            seen_on_pages[(xref, w, h)] += 1
        placements = [p for p in placements if seen_on_pages[(p[4], p[2], p[3])] < 2]

        found: List[FoundImage] = []
        for page_no, bbox, width, height, _ in placements[:MAX_IMAGES]:
            page = doc[page_no - 1]
            dpi = int(min(MAX_DPI, max(MIN_DPI, width / max(bbox.width / 72, 1))))
            pix = page.get_pixmap(clip=bbox, dpi=dpi)
            path = out_dir / f"page{page_no}_image{len(found) + 1}.png"
            pix.save(str(path))
            found.append(FoundImage(str(path), page_no, pix.width, pix.height, _caption_near(page, bbox)))
    finally:
        doc.close()

    return _rank(found, instruction)


def extract_docx_images(docx_path: str, out_dir: Path) -> List[FoundImage]:
    """Pictures embedded in a Word document, in reading order (icons and tiny images skipped)."""
    import zipfile

    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    found: List[FoundImage] = []
    with zipfile.ZipFile(docx_path) as archive:
        names = set(archive.namelist())
        rels = archive.read("word/_rels/document.xml.rels").decode("utf-8", "replace")
        body = archive.read("word/document.xml").decode("utf-8", "replace")
        targets = dict(re.findall(r'Id="(rId\d+)"[^>]*?Target="(media/[^"]+)"', rels))
        targets.update({i: t for t, i in re.findall(r'Target="(media/[^"]+)"[^>]*?Id="(rId\d+)"', rels)})

        ordered, seen = [], set()
        for rid in re.findall(r'r:embed="(rId\d+)"', body):   # order of appearance in the text
            target = targets.get(rid)
            if target and target not in seen:
                seen.add(target)
                ordered.append("word/" + target)

        for member in ordered:
            suffix = Path(member).suffix.lower()
            if member not in names or suffix not in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
                continue
            path = out_dir / f"image{len(found) + 1}{suffix}"
            path.write_bytes(archive.read(member))
            try:
                with Image.open(path) as img:
                    width, height = img.size
            except Exception:
                path.unlink(missing_ok=True)
                continue
            if width < MIN_WIDTH_PX or height < MIN_HEIGHT_PX:
                path.unlink(missing_ok=True)
                continue
            found.append(FoundImage(str(path), 0, width, height, ""))
            if len(found) >= MAX_IMAGES:
                break
    return found


def _rank(found: List[FoundImage], instruction: str) -> List[FoundImage]:
    """Put pictures whose caption matches the instruction's words first; keep page order otherwise."""
    words = {w for w in re.findall(r"[a-z]{4,}", instruction.lower())} - {
        "give", "show", "download", "image", "images", "picture", "pictures", "graph", "graphs",
        "chart", "charts", "figure", "figures", "this", "that", "with", "from", "please", "document",
    }
    if not words:
        return found
    return sorted(found, key=lambda f: (-len(words & set(re.findall(r"[a-z]{4,}", f.caption.lower()))), f.page))
