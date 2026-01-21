import os
import csv
import fitz
import docx
import pandas as pd
from PIL import Image
from typing import Iterator, Tuple

from app.ocr import safe_ocr


def iterateFilePages(filePath: str) -> Iterator[Tuple[int, str, bool]]:
    """
    Yield (pageNumber, text, ocrUsed) for supported file types.
    """
    extension = os.path.splitext(filePath)[1].lower()

    if extension == ".pdf":
        yield from extractPdf(filePath)
    elif extension == ".docx":
        yield from extractDocx(filePath)
    elif extension in [".xls", ".xlsx"]:
        yield from extractExcel(filePath)
    elif extension == ".csv":
        yield from extractCsv(filePath)
    elif extension == ".txt":
        yield from extractTxt(filePath)
    elif extension in [".png", ".jpg", ".jpeg"]:
        yield from extractImage(filePath)
    else:
        raise ValueError(f"Unsupported file type: {extension}")


# =========================
# EXTRACTORS
# =========================


def extractPdf(path: str):
    document = fitz.open(path)
    pageNumber = 1

    for page in document:
        text_blocks = []

        # 1️⃣ Extract text blocks
        blocks = page.get_text("blocks")
        for block in blocks:
            block_text = block[4].strip()
            if block_text:
                text_blocks.append(block_text)

        if text_blocks:
            yield pageNumber, "\n".join(text_blocks), False
        else:
            # 2️⃣ OCR fallback (image-heavy page)
            pix = page.get_pixmap(dpi=200)
            image = Image.frombytes(
                "RGB",
                (pix.width, pix.height),
                pix.samples,
            )
            ocrText = safe_ocr(image)
            if ocrText.strip():
                yield pageNumber, ocrText.strip(), True

        pageNumber += 1


def extractDocx(path: str):
    document = docx.Document(path)
    buffer = []
    pageNumber = 1

    def flush():
        nonlocal buffer, pageNumber
        if buffer:
            yield pageNumber, "\n".join(buffer), False
            buffer = []
            pageNumber += 1

    # Text paragraphs
    for para in document.paragraphs:
        text = para.text.strip()
        if text:
            buffer.append(text)
        if len(buffer) >= 6:
            yield from flush()

    yield from flush()

    # Embedded images → OCR
    for rel in document.part._rels.values():
        if "image" in rel.reltype:
            try:
                image = Image.open(io.BytesIO(rel.target_part.blob))
                ocrText = safe_ocr(image)
                if ocrText.strip():
                    yield pageNumber, ocrText.strip(), True
                    pageNumber += 1
            except Exception:
                continue


def extractExcel(path: str):
    sheets = pd.read_excel(path, sheet_name=None)
    pageNumber = 1

    for sheetName, df in sheets.items():
        df = df.dropna(how="all")
        if df.empty:
            continue

        rows = []
        for _, row in df.iterrows():
            values = [str(v) for v in row if pd.notna(v)]
            if values:
                rows.append(" | ".join(values))

            # Flush every ~10 rows
            if len(rows) >= 10:
                yield pageNumber, f"[SHEET: {sheetName}]\n" + "\n".join(rows), False
                rows = []
                pageNumber += 1

        if rows:
            yield pageNumber, f"[SHEET: {sheetName}]\n" + "\n".join(rows), False
            pageNumber += 1


def extractCsv(path: str):
    rows = []
    pageNumber = 1

    with open(path, encoding="utf-8", errors="ignore") as file:
        reader = csv.reader(file)
        for row in reader:
            if row:
                rows.append(" | ".join(row))

            if len(rows) >= 15:
                yield pageNumber, "\n".join(rows), False
                rows = []
                pageNumber += 1

    if rows:
        yield pageNumber, "\n".join(rows), False


def extractTxt(path: str):
    with open(path, encoding="utf-8", errors="ignore") as file:
        lines = [line.strip() for line in file if line.strip()]

    buffer = []
    pageNumber = 1

    for line in lines:
        buffer.append(line)
        if len(buffer) >= 10:
            yield pageNumber, "\n".join(buffer), False
            buffer = []
            pageNumber += 1

    if buffer:
        yield pageNumber, "\n".join(buffer), False


def extractImage(path: str):
    image = Image.open(path)
    text = safe_ocr(image)

    if text.strip():
        yield 1, text, True
