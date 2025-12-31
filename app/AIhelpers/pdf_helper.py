import fitz  # PyMuPDF
import pytesseract
from typing import Iterator, Tuple
from PIL import Image


def iter_pdf_pages(pdf_path: str, dpi: int = 300) -> Iterator[Tuple[str, bool]]:
    
    doc = fitz.open(pdf_path)

    for page in doc:
        text = page.get_text().strip()

        # If page has substantial text, return it directly
        if len(text) >= 50:
            yield text, False
            continue

        # Otherwise render page to image and OCR only that page
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
            mode = "RGB" if pix.n < 4 else "RGBA"
            img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
            if mode == "RGBA":
                img = img.convert("RGB")
            ocr_text = pytesseract.image_to_string(img)
            yield ocr_text.strip(), True
        except Exception:
            # Fall back to whatever text we have (even if short)
            yield text, False


def extract_pdf_text(pdf_path: str) -> Tuple[str, bool]:
    """
    Backwards-compatible helper that returns the full text and whether OCR was used anywhere.
    This still streams pages internally but aggregates to a single string for callers that need it.
    """
    texts = []
    ocr_used = False
    for page_text, page_ocr in iter_pdf_pages(pdf_path):
        texts.append(page_text)
        if page_ocr:
            ocr_used = True

    return "\n".join(texts).strip(), ocr_used