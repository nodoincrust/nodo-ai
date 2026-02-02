import os
from tkinter import Image
import pytesseract
import logging
from pytesseract import Output
from PIL import Image
logger = logging.getLogger("ocr")

TESSERACT_CMD = os.getenv("TESSERACT_CMD")

if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    logger.info(f"Tesseract configured: {TESSERACT_CMD}")
else:
    logger.warning("TESSERACT_CMD not set – OCR will be disabled")

def preprocess_for_ocr(image: Image.Image) -> Image.Image:
    image = image.convert("L")  # grayscale
    image = image.point(lambda x: 0 if x < 160 else 255, "1")  # binarize
    return image


def safe_ocr(image: Image.Image, min_confidence: int = 30) -> tuple[str, bool]:
    try:
        image = preprocess_for_ocr(image)

        data = pytesseract.image_to_data(image, output_type=Output.DICT)

        texts = []
        confidences = []

        for txt, conf in zip(data.get("text", []), data.get("conf", [])):
            if not txt or not txt.strip():
                continue
            try:
                c = int(conf)
                if c >= 0:
                    texts.append(txt.strip())
                    confidences.append(c)
            except ValueError:
                continue

        if not texts:
            return "", False

        avg_conf = sum(confidences) / max(len(confidences), 1)

        if avg_conf < min_confidence and len(texts) < 5:
            logger.info("OCR rejected (avg_conf=%.2f)", avg_conf)
            return "", False

        return " ".join(texts), True

    except Exception as exc:
        logger.warning("OCR failed safely: %s", exc)
        return "", False