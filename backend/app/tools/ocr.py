"""
OCR tool: extract text from a document.

- Digital PDFs (real embedded text layer): extracted directly with PyMuPDF,
  no OCR needed -- fast and exact.
- Scanned PDFs (no embedded text layer) and plain images: rendered to
  bitmap and run through PaddleOCR.
"""

from __future__ import annotations

MIN_CHARS_FOR_DIGITAL_PDF = 20  # below this, treat the PDF as "no text layer"

_paddle_ocr_engine = None


def _get_paddle_ocr():
    """Lazily construct the PaddleOCR engine (slow to import/initialize)."""
    global _paddle_ocr_engine
    if _paddle_ocr_engine is None:
        from paddleocr import PaddleOCR

        # PaddleOCR 3.x argument names. The doc-orientation and unwarping
        # sub-models are disabled: they add load time and are aimed at
        # skewed full-page scans, which we don't need for the common case.
        _paddle_ocr_engine = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _paddle_ocr_engine


def _ocr_image_file(file_path: str) -> str:
    engine = _get_paddle_ocr()
    lines = []
    for page_result in engine.predict(input=file_path):
        lines.extend(page_result["rec_texts"])
    return "\n".join(lines)


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
    if file_path.lower().endswith(".pdf"):
        import fitz  # PyMuPDF

        doc = fitz.open(file_path)
        digital_text_parts = [page.get_text() for page in doc]
        digital_text = "\n".join(digital_text_parts).strip()

        if len(digital_text) >= MIN_CHARS_FOR_DIGITAL_PDF:
            doc.close()
            return digital_text

        # No usable text layer -- OCR each page image instead.
        ocr_parts = [_ocr_pdf_page_as_image(page) for page in doc]
        doc.close()
        return "\n".join(ocr_parts).strip()

    return _ocr_image_file(file_path)
