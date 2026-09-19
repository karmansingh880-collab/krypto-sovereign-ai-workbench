"""
OCR tool: extract text from a document.

- Digital PDFs (real embedded text layer): extracted directly with PyMuPDF,
  no OCR needed -- fast and exact.
- Scanned PDFs (no embedded text layer) and plain images: rendered to
  bitmap and run through PaddleOCR.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

MIN_CHARS_FOR_DIGITAL_PDF = 20  # below this, treat the PDF as "no text layer"

# Limits so a very large input can't run for hours or exhaust memory.
MAX_OCR_PAGES = 40        # scanned PDFs: OCR at most this many pages (each takes seconds on a CPU)
MAX_IMAGE_SIDE = 3000     # images larger than this (pixels, longest side) are shrunk before OCR
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".log")

_paddle_ocr_engine = None

# OCR is the slowest thing the app does on a CPU (tens of seconds per image), and the same file is
# often read again (a re-run, a follow-up question). Results are remembered by the file's content.
CACHE_DIR = Path.home() / ".krypto_cache" / "ocr"
_CACHE_VERSION = "ocr-v1"
_CACHE_MAX_FILES = 300


def _content_key(file_path: str) -> str:
    digest = hashlib.sha256(f"{_CACHE_VERSION}|{MAX_OCR_PAGES}|{MAX_IMAGE_SIDE}|".encode())
    with open(file_path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_get(key: str) -> Optional[str]:
    try:
        return (CACHE_DIR / f"{key}.txt").read_text(encoding="utf-8")
    except OSError:
        return None


def _cache_put(key: str, text: str) -> None:
    if not text.strip():
        return  # an empty result may be a hiccup; don't remember it
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        target = CACHE_DIR / f"{key}.txt"
        temp = target.with_suffix(".tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(target)
        files = sorted(CACHE_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime)
        for old in files[:-_CACHE_MAX_FILES]:
            old.unlink(missing_ok=True)
    except OSError:
        pass  # caching is only a speed-up


def _get_paddle_ocr():
    """Lazily construct the PaddleOCR engine (slow to import/initialize)."""
    global _paddle_ocr_engine
    if _paddle_ocr_engine is None:
        from backend.app import netguard

        netguard.harden_environment()  # e.g. don't probe model hosts on start-up: the models are local
        from paddleocr import PaddleOCR

        # PaddleOCR 3.x argument names. The doc-orientation and unwarping
        # sub-models are disabled: they add load time and are aimed at
        # skewed full-page scans, which we don't need for the common case.
        _paddle_ocr_engine = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # Paddle 3.x's oneDNN CPU path crashes on Windows
            # ("ConvertPirAttribute2RuntimeAttribute not support ...").
            enable_mkldnn=False,
        )
    return _paddle_ocr_engine


def _shrunk_copy_if_huge(file_path: str):
    """A temp PNG no larger than MAX_IMAGE_SIDE, or None if the image is already small enough."""
    import os
    import tempfile

    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None  # we resize right below; don't let Pillow refuse big scans
    with Image.open(file_path) as img:
        if max(img.size) <= MAX_IMAGE_SIDE:
            return None
        scale = MAX_IMAGE_SIDE / max(img.size)
        small = img.convert("RGB").resize((int(img.width * scale), int(img.height * scale)))
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    small.save(tmp_path)
    return tmp_path


def _ocr_image_file(file_path: str) -> str:
    import os

    engine = _get_paddle_ocr()
    shrunk = _shrunk_copy_if_huge(file_path)
    try:
        lines = []
        for page_result in engine.predict(input=shrunk or file_path):
            lines.extend(page_result["rec_texts"])
        return "\n".join(lines)
    finally:
        if shrunk:
            os.unlink(shrunk)


def _extract_docx(file_path: str) -> str:
    """Text of a Word document: paragraphs and tables, in reading order."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(file_path)
    parts = []
    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            text = Paragraph(child, doc).text
            if text.strip():
                parts.append(text)
        elif child.tag.endswith("}tbl"):
            for row in Table(child, doc).rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
    return "\n".join(parts).strip()


def _read_plain_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def _ocr_pdf_page_as_image(page) -> str:
    import os
    import tempfile

    pix = page.get_pixmap(dpi=300)

    # A unique temp file per call: the FastAPI backend can OCR several
    # documents concurrently, and a shared fixed path would let one
    # request overwrite another's rendered page.
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        pix.save(tmp_path)
        return _ocr_image_file(tmp_path)
    finally:
        os.unlink(tmp_path)


def extract_text(file_path: str) -> str:
    """
    Extract text from a PDF or image file.

    PDFs with a real text layer are read directly via PyMuPDF. PDFs
    without one (scanned documents) and plain image files fall back to
    PaddleOCR.
    """
    lower = file_path.lower()
    if lower.endswith(".docx"):
        return _extract_docx(file_path)
    if lower.endswith(TEXT_SUFFIXES):
        return _read_plain_text(file_path)

    if lower.endswith(".pdf"):
        import fitz  # PyMuPDF

        doc = fitz.open(file_path)
        digital_text_parts = [page.get_text() for page in doc]
        digital_text = "\n".join(digital_text_parts).strip()

        if len(digital_text) >= MIN_CHARS_FOR_DIGITAL_PDF:
            doc.close()
            return digital_text

        # No usable text layer -- OCR each page image instead (up to MAX_OCR_PAGES).
        key = _content_key(file_path)
        cached = _cache_get(key)
        if cached is not None:
            doc.close()
            return cached
        page_count = len(doc)
        ocr_parts = [_ocr_pdf_page_as_image(doc[i]) for i in range(min(page_count, MAX_OCR_PAGES))]
        doc.close()
        if page_count > MAX_OCR_PAGES:
            ocr_parts.append(
                f"[Scanned PDF: only the first {MAX_OCR_PAGES} of {page_count} pages were read.]"
            )
        text = "\n".join(ocr_parts).strip()
        _cache_put(key, text)
        return text

    key = _content_key(file_path)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    text = _ocr_image_file(file_path)
    _cache_put(key, text)
    return text
