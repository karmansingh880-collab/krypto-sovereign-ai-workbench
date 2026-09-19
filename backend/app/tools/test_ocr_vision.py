"""
Manual smoke test for ocr.py and vision.py.

Generates its own tiny test files (a text image and a digital PDF) with
PIL/PyMuPDF so it doesn't depend on a file already existing on disk.

Run with:
    ./venv/bin/python -m backend.app.tools.test_ocr_vision
"""

import tempfile
from pathlib import Path

from backend.app.tools.ocr import extract_text
from backend.app.tools.vision import describe_image


def make_test_image(path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (400, 120), color="white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 40), "HELLO KRYPTO OCR TEST", fill="black")
    image.save(path)


def make_digital_pdf(path: str) -> None:
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "This is a digital PDF with a real text layer.")
    doc.save(path)
    doc.close()


def main():
    tmp_dir = tempfile.mkdtemp()
    image_path = str(Path(tmp_dir) / "test_image.png")
    pdf_path = str(Path(tmp_dir) / "test_digital.pdf")

    print("Generating test image with embedded text...")
    make_test_image(image_path)
    print(f"  -> {image_path}")

    print("Generating digital (text-layer) PDF...")
    make_digital_pdf(pdf_path)
    print(f"  -> {pdf_path}")

    print("\n--- extract_text() on digital PDF (should use PyMuPDF path) ---")
    pdf_text = extract_text(pdf_path)
    print(repr(pdf_text))

    print("\n--- extract_text() on image (should use PaddleOCR path) ---")
    try:
        image_text = extract_text(image_path)
        print(repr(image_text))
    except Exception as exc:
        print(f"  OCR path failed: {exc}")

    print("\n--- describe_image() via qwen2.5vl:7b ---")
    try:
        answer = describe_image(image_path, "What text do you see in this image?")
        print(answer)
    except Exception as exc:
        print(f"  Vision call failed: {exc}")


if __name__ == "__main__":
    main()
