import fitz  # PyMuPDF
import pytesseract
from pdf2image import convert_from_path
from typing import Tuple


def extract_text_with_ocr(pdf_path: str) -> str:
    """
    OCR fallback for scanned PDFs
    """
    images = convert_from_path(pdf_path)
    return "\n".join(
        pytesseract.image_to_string(img) for img in images
    )


def extract_pdf_text(pdf_path: str) -> Tuple[str, bool]:
    # Uses OCR if text is not extractable.

    doc = fitz.open(pdf_path)
    text = "".join(page.get_text() for page in doc)

    if len(text.strip()) < 50:
        return extract_text_with_ocr(pdf_path), True

    return text.strip(), False
