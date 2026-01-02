import os
import csv
import fitz
import docx
import pandas as pd
from PIL import Image

# ✅ SAFE OCR (centralized, env-based)
from app.ocr import safe_ocr


def iter_file_pages(file_path: str):
    """
    Yields: (page_number, text, ocr_used)
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        yield from extract_pdf(file_path)
    elif ext == ".docx":
        yield from extract_docx(file_path)
    elif ext in [".xls", ".xlsx"]:
        yield from extract_excel(file_path)
    elif ext == ".csv":
        yield from extract_csv(file_path)
    elif ext == ".txt":
        yield from extract_txt(file_path)
    elif ext in [".png", ".jpg", ".jpeg"]:
        yield from extract_image(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


# =========================
# EXTRACTORS
# =========================

def extract_pdf(path):
    doc = fitz.open(path)

    for page_no, page in enumerate(doc, start=1):
        text = page.get_text().strip()

        if text:
            yield page_no, text, False
        else:
            # OCR fallback (SAFE)
            pix = page.get_pixmap()
            img = Image.frombytes(
                "RGB",
                [pix.width, pix.height],
                pix.samples
            )
            ocr_text = safe_ocr(img)
            if ocr_text.strip():
                yield page_no, ocr_text, True


def extract_docx(path):
    """
    Generic DOCX extractor:
    - Deduplicates repeated layout text
    - Works for resumes, reports, tables, mixed content
    """
    d = docx.Document(path)

    seen = set()
    lines = []

    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue

        t = " ".join(t.split())
        key = t.lower()

        if key in seen:
            continue

        seen.add(key)
        lines.append(t)

    if lines:
        yield 1, "\n".join(lines), False


def extract_excel(path):
    sheets = pd.read_excel(path, sheet_name=None)
    page = 1

    for sheet_name, df in sheets.items():
        df = df.dropna(how="all")
        if df.empty:
            continue

        rows = []
        for _, row in df.iterrows():
            values = [str(v) for v in row if pd.notna(v)]
            if values:
                rows.append(" | ".join(values))

        if rows:
            text = f"[SHEET: {sheet_name}]\n" + "\n".join(rows)
            yield page, text, False
            page += 1


def extract_csv(path):
    rows = []

    with open(path, encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for row in reader:
            if row:
                rows.append(" | ".join(row))

    if rows:
        yield 1, "\n".join(rows), False


def extract_txt(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read().strip()
        if text:
            yield 1, text, False


def extract_image(path):
    img = Image.open(path)

    # SAFE OCR — no crash if tesseract missing
    text = safe_ocr(img)

    if text.strip():
        yield 1, text, True
