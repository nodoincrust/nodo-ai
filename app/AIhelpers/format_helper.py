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

    for pageNumber, page in enumerate(document, start=1):
        text = page.get_text().strip()

        if text:
            yield pageNumber, text, False
        else:
            pix = page.get_pixmap()
            image = Image.frombytes(
                "RGB",
                (pix.width, pix.height),
                pix.samples,
            )
            ocrText = safe_ocr(image)
            if ocrText.strip():
                yield pageNumber, ocrText, True


def extractDocx(path: str):
    """
    Generic DOCX extractor with deduplication.
    """
    document = docx.Document(path)
    seen = set()
    lines = []

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue

        text = " ".join(text.split())
        key = text.lower()

        if key in seen:
            continue

        seen.add(key)
        lines.append(text)

    if lines:
        yield 1, "\n".join(lines), False


def extractExcel(path: str):
    sheets = pd.read_excel(path, sheet_name=None)
    pageNumber = 1

    for sheetName, dataframe in sheets.items():
        dataframe = dataframe.dropna(how="all")
        if dataframe.empty:
            continue

        rows = []
        for _, row in dataframe.iterrows():
            values = [str(value) for value in row if pd.notna(value)]
            if values:
                rows.append(" | ".join(values))

        if rows:
            text = f"[SHEET: {sheetName}]\n" + "\n".join(rows)
            yield pageNumber, text, False
            pageNumber += 1


def extractCsv(path: str):
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as file:
        reader = csv.reader(file)
        for row in reader:
            if row:
                rows.append(" | ".join(row))

    if rows:
        yield 1, "\n".join(rows), False


def extractTxt(path: str):
    with open(path, encoding="utf-8", errors="ignore") as file:
        text = file.read().strip()
        if text:
            yield 1, text, False


def extractImage(path: str):
    image = Image.open(path)
    text = safe_ocr(image)

    if text.strip():
        yield 1, text, True