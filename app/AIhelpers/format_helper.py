import os
import logging
from typing import Iterator, Tuple

from PIL import Image, ImageOps
import pytesseract

# Setup Logger
logger = logging.getLogger("ai_modul.format_helper")

import os
import pytesseract
import logging

logger = logging.getLogger("ai_modul.format_helper")

TESSERACT_CMD = os.getenv("TESSERACT_CMD")

if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    logger.info("Using Tesseract from ENV: %s", TESSERACT_CMD)
else:
    logger.info("Using system Tesseract (PATH)")

try:
    import docx
except Exception:
    docx = None

try:
    import pandas as pd
except Exception:
    pd = None

from .pdf_helper import iter_pdf_pages

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif'}

def _ocr_image(path: str) -> str:
    """Performs OCR on an image with preprocessing fallbacks."""
    logger.info("OCR: Processing image file: %s", path)
    try:
        img = Image.open(path)
    except Exception as e:
        logger.error("OCR: Failed to open image: %s", e)
        return ''

    texts = []
    n_frames = getattr(img, "n_frames", 1)
    
    for frame in range(n_frames):
        try:
            if n_frames > 1:
                img.seek(frame)

            frame_img = img.convert('RGB')

            # 1. Try baseline OCR
            txt = pytesseract.image_to_string(frame_img).strip()
            if txt:
                logger.info("OCR: Text detected using baseline on frame %d", frame)
                texts.append(txt)
                continue

            # 2. Preprocess: grayscale, resize, autocontrast
            logger.info("OCR: No text found in baseline. Attempting preprocessing on frame %d", frame)
            gray = ImageOps.grayscale(frame_img)
            w, h = gray.size
            up = gray.resize((min(4000, w * 2), min(4000, h * 2)))
            up = ImageOps.autocontrast(up)

            # Try thresholding
            try:
                bw = up.point(lambda p: 255 if p > 160 else 0)
            except Exception:
                bw = up

            txt2 = pytesseract.image_to_string(bw).strip()
            if txt2:
                texts.append(txt2)
            else:
                # final fallback to resized grayscale
                txt3 = pytesseract.image_to_string(up).strip()
                texts.append(txt3)

        except Exception as e:
            logger.error("OCR: Frame %d processing failed: %s", frame, e)
            continue

    final_text = "\n".join(t for t in texts if t).strip()
    logger.info("OCR: Completed. Total characters extracted: %d", len(final_text))
    return final_text

def iter_file_pages(file_path: str, file_type: str = None) -> Iterator[Tuple[str, bool]]:
    """
    Yield (text, ocr_used_flag) for the given file. 
    Supports PDFs, docx, images, xlsx, csv.
    """
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    file_type = (file_type or '').lower()

    # 1. PDFs
    if ext == '.pdf' or 'pdf' in file_type:
        logger.info("Routing to PDF handler: %s", file_path)
        for t, o in iter_pdf_pages(file_path):
            yield t, o
        return

    # 2. DOCX
    if (ext == '.docx') or (file_type and 'word' in file_type):
        logger.info("Routing to DOCX handler: %s", file_path)
        if docx is None:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    yield f.read(), False
                    return
            except Exception:
                yield '', False
                return

        doc = docx.Document(file_path)
        paragraph_texts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        block_size = 40
        for i in range(0, len(paragraph_texts), block_size):
            yield '\n'.join(paragraph_texts[i:i + block_size]), False
        return

    # 3. Images (Explicit OCR Branch)
    if ext in IMAGE_EXTS or (file_type and 'image' in file_type):
        logger.info("Routing to Image OCR handler: %s", file_path)
        try:
            text = _ocr_image(file_path)
            yield text, True
        except Exception as e:
            logger.error("Image OCR routing failed: %s", e)
            yield '', False
        return

    # 4. Excel
    if (ext in {'.xls', '.xlsx'}) or (file_type and 'excel' in file_type):
        logger.info("Routing to Excel handler: %s", file_path)
        if pd is None:
            yield '', False
            return
        try:
            sheets = pd.read_excel(file_path, sheet_name=None)
            for name, df in sheets.items():
                rows = []
                for r in df.fillna('').astype(str).values:
                    rows.append(' '.join(r))
                yield '\n'.join(rows), False
        except Exception as e:
            logger.error("Excel processing failed: %s", e)
            yield '', False
        return

    # 5. CSV or Text
    if ext in {'.csv', '.txt'} or (file_type and ('csv' in file_type or 'text' in file_type)):
        logger.info("Routing to Text/CSV handler: %s", file_path)
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                buf = []
                for line in f:
                    buf.append(line.rstrip('\n'))
                    if len(buf) >= 2000:
                        yield '\n'.join(buf), False
                        buf = []
                if buf:
                    yield '\n'.join(buf), False
        except Exception:
            yield '', False
        return

    # Fallback
    logger.warning("No specific handler for %s. Attempting OCR fallback.", ext)
    try:
        text = _ocr_image(file_path)
        if text:
            yield text, True
            return
    except Exception:
        pass

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            yield f.read(), False
    except Exception:
        yield '', False