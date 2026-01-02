import fitz  # PyMuPDF
from typing import Iterator, Tuple
from PIL import Image

from app.ocr import safe_ocr


def extract_text_with_ocr(pdf_path: str) -> str:
    """
    OCR fallback for scanned PDFs (SAFE).
    Returns aggregated text.
    """
    texts = []
    for text, _ in iter_pdf_pages(pdf_path):
        if text:
            texts.append(text)
    return "\n".join(texts)


def extract_pdf_text(pdf_path: str) -> Tuple[str, bool]:
    """
    Backwards-compatible helper.
    Returns (full_text, ocr_used_anywhere).
    """
    texts = []
    ocr_used = False

    for text, used_ocr in iter_pdf_pages(pdf_path):
        if text:
            texts.append(text)
        ocr_used = ocr_used or used_ocr

    return "\n".join(texts).strip(), ocr_used


def iter_pdf_pages(
    pdf_path: str,
    dpi: int = 150,
    min_text_len: int = 50,
) -> Iterator[Tuple[str, bool]]:
    """
    Yield (text, ocr_used) per page.
    OCR is used only when extracted text is insufficient.
    SAFE: never crashes if OCR is unavailable.
    """
    doc = fitz.open(pdf_path)

    for page in doc:
        text = page.get_text().strip()

        # If sufficient text exists, skip OCR
        if len(text) >= min_text_len:
            yield text, False
            continue

        # OCR fallback (SAFE)
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
            mode = "RGB" if pix.n < 4 else "RGBA"
            img = Image.frombytes(
                mode,
                (pix.width, pix.height),
                pix.samples
            )
            if mode == "RGBA":
                img = img.convert("RGB")

            ocr_text = safe_ocr(img)
            yield ocr_text.strip(), bool(ocr_text.strip())

        except Exception:
            # Absolute fallback: return what we have
            yield text, False