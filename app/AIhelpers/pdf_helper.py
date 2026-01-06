import fitz  # PyMuPDF
from typing import Iterator, Tuple
from PIL import Image

from app.ocr import safe_ocr


def extractTextWithOcr(pdfPath: str) -> str:
    """
    OCR fallback for scanned PDFs.
    Returns aggregated text.
    """
    texts = []
    for text, _ in iteratePdfPages(pdfPath):
        if text:
            texts.append(text)
    return "\n".join(texts)


def extractPdfText(pdfPath: str) -> Tuple[str, bool]:
    """
    Backward-compatible helper.
    Returns (fullText, ocrUsedAnywhere).
    """
    texts = []
    ocrUsed = False

    for text, usedOcr in iteratePdfPages(pdfPath):
        if text:
            texts.append(text)
        ocrUsed = ocrUsed or usedOcr

    return "\n".join(texts).strip(), ocrUsed


def iteratePdfPages(
    pdfPath: str,
    dpi: int = 150,
    minTextLength: int = 50,
) -> Iterator[Tuple[str, bool]]:
    """
    Yield (text, ocrUsed) per page.
    OCR is used only when extracted text is insufficient.
    SAFE: never crashes if OCR is unavailable.
    """
    document = fitz.open(pdfPath)

    for page in document:
        text = page.get_text().strip()

        if len(text) >= minTextLength:
            yield text, False
            continue

        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
            mode = "RGB" if pix.n < 4 else "RGBA"
            image = Image.frombytes(
                mode,
                (pix.width, pix.height),
                pix.samples,
            )
            if mode == "RGBA":
                image = image.convert("RGB")

            ocrText = safe_ocr(image)
            yield ocrText.strip(), bool(ocrText.strip())

        except Exception:
            yield text, False