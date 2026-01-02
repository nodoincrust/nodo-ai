import uuid
import logging
import shutil
import os
from typing import List, Dict
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.db import SessionLocal
from app.models import (
    AIDocument,
    DocuementChunks,
    DocumentVersion,
    DocumentReview,
    Document,
    Company,
)
from app.AIhelpers.chunk_helper import chunk_text
from app.AIhelpers.format_helper import iter_file_pages
from app.schemas import DocumentSaveSchema

BASE_STORAGE_PATH = "storage"
logger = logging.getLogger(__name__)

# ==============================
# INGESTION CONFIG
# ==============================

MAX_UPLOAD_MB = 50
CHUNK_BATCH_SIZE = 32


# ==============================
# HELPERS
# ==============================

def normalize_content(text: str) -> str:
    """
    Detect tabular vs textual content and normalize
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ""

    numeric_ratio = sum(any(c.isdigit() for c in l) for l in lines) / len(lines)
    short_ratio = sum(len(l.split()) <= 5 for l in lines) / len(lines)

    if numeric_ratio > 0.35 or short_ratio > 0.6:
        return "\n".join(f"[ROW] {l}" for l in lines)
    else:
        return "\n".join(f"[TEXT] {l}" for l in lines)


# ==============================
# MAIN INGESTION PIPELINE
# ==============================

def process_document(
    *,
    file_path: str,
    document_id: str,
    filename: str,
    session_id: str | None,
    file_type: str,
    file_size_mb: float,
) -> Dict:
    """
    Core ingestion pipeline:
    - Create AIDocument (FK parent)
    - Extract text (all formats)
    - Normalize
    - Chunk
    - Store chunks
    """

    db: Session = SessionLocal()
    chunks_created = 0
    ocr_used = False

    try:
        # --------------------------------------------------
        # 1️⃣ CREATE AI DOCUMENT (FK PARENT)  🔥 REQUIRED
        # --------------------------------------------------
        ai_doc = AIDocument(
            document_id=document_id,
            session_id=session_id,
            filename=filename,
            file_type=file_type,
            file_size_mb=file_size_mb,
        )
        db.add(ai_doc)
        db.commit()  # 🔴 MUST COMMIT BEFORE CHUNKS

        logger.info(f"AIDocument created: {document_id}")

        # --------------------------------------------------
        # 2️⃣ EXTRACT + CHUNK
        # --------------------------------------------------
        for page_no, raw_text, used_ocr in iter_file_pages(file_path):
            if not raw_text or not raw_text.strip():
                continue

            ocr_used = ocr_used or used_ocr
            normalized = normalize_content(raw_text)

            for chunk in chunk_text(normalized):
                db.add(
                    DocuementChunks(
                        id=uuid.uuid4(),
                        document_id=document_id,
                        session_id=session_id,
                        chunk_index=chunks_created,
                        page_number=page_no,
                        chunk_text=chunk,
                    )
                )
                chunks_created += 1

        db.commit()

        logger.info(
            f"Document {document_id}: {chunks_created} chunks stored | OCR={ocr_used}"
        )

        return {
            "chunks": chunks_created,
            "ocr_used": ocr_used,
        }

    except Exception as e:
        db.rollback()
        logger.exception("Document ingestion failed")
        raise

    finally:
        db.close()


# ==============================
# DOCUMENT SAVE (BUSINESS FLOW)
# ==============================

def save_document(
    db: Session,
    document_id: int,
    payload: DocumentSaveSchema,
    current_user: dict,
):
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.is_delete.is_(False),
        )
        .first()
    )

    if not document:
        raise HTTPException(404, "Document not found")

    if document.uploaded_by != current_user["user_id"]:
        raise HTTPException(403, "Permission denied")

    if document.status != "DRAFT":
        raise HTTPException(400, "Only draft documents can be saved")

    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    if not version:
        raise HTTPException(500, "Document version not found")

    if payload.summary is not None:
        version.summary = payload.summary

    if payload.tags is not None:
        version.tags = payload.tags

    document.status = "SUBMITTED"

    review = DocumentReview(
        document_id=document.id,
        reviewed_by=None,
        status="PENDING",
    )

    db.add(review)
    db.commit()

    return {
        "document_id": document.id,
        "status": document.status,
    }


# ==============================
# DRAFT CREATION
# ==============================

def create_document_draft(
    db: Session,
    *,
    ai_document_id: str,
    temp_file_path: str,
    original_filename: str,
    department_id: int,
    current_user: dict,
):
    company = (
        db.query(Company)
        .filter(
            Company.id == current_user["company_id"],
            Company.is_delete.is_(False),
        )
        .first()
    )

    if not company:
        raise HTTPException(404, "Company not found")

    document = Document(
        company_id=company.id,
        department_id=department_id,
        uploaded_by=current_user["user_id"],
        status="DRAFT",
    )
    db.add(document)
    db.flush()

    doc_dir = os.path.join(
        BASE_STORAGE_PATH,
        "companies",
        str(company.id),
        "documents",
        str(document.id),
    )
    os.makedirs(doc_dir, exist_ok=True)

    permanent_path = os.path.join(
        doc_dir,
        f"v1_{original_filename}",
    )

    shutil.move(temp_file_path, permanent_path)

    file_size_bytes = os.path.getsize(permanent_path)

    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        file_path=permanent_path,
        file_name=original_filename,
        file_size_bytes=file_size_bytes,
        ai_document_id=ai_document_id,
        created_by=current_user["user_id"],
    )

    db.add(version)
    company.remaining_space -= file_size_bytes
    db.commit()

    return document.id
