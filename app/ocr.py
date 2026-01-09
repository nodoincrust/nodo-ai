import os
import pytesseract
import logging

logger = logging.getLogger("ocr")

TESSERACT_CMD = os.getenv("TESSERACT_CMD")

if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    logger.info(f"Tesseract configured: {TESSERACT_CMD}")
else:
    logger.warning("TESSERACT_CMD not set – OCR will be disabled")

#fail-safe OCR function
def safe_ocr(image) -> str:
    try:
        if not TESSERACT_CMD:
            return ""
        return pytesseract.image_to_string(image) #Runs OCR and return extracted text
    except Exception as e:
        logger.warning(f"OCR failed: {e}")
        return ""
