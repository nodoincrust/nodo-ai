import uuid
import logging
import shutil
import os
from typing import Dict
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.db import SessionLocal
from app.models import (
    Document,
    AIDocument,
    ChatSession,
    DocumentChunk,
    DocumentVersion,
    DocumentReview,
    Company,
)
from app.AIhelpers.chunk_helper import chunkText
from app.AIhelpers.format_helper import iterateFilePages
from app.schemas import DocumentSaveSchema

BASE_STORAGE_PATH = "storage"
logger = logging.getLogger(__name__)

MAX_UPLOAD_MB = 50
CHUNK_BATCH_SIZE = 32


# ======================================================
# MAIN AI INGESTION PIPELINE
# ======================================================

def processDocument(
    *,
    filePath: str,
    documentId: int,
    filename: str,
    fileType: str,
    fileSizeMb: float,
) -> Dict:
    """
    AI ingestion pipeline.

    🔒 GUARANTEES:
    - Session is created BEFORE AIDocument
    - ai_documents.session_id is NEVER NULL
    - One document → one session
    """

    if not isinstance(documentId, int):
        raise TypeError(
            f"documentId must be int, got {type(documentId)} → {documentId}"
        )

    db: Session = SessionLocal()
    chunksCreated = 0
    ocrUsed = False

    try:
        # --------------------------------------------------
        # 1️⃣ CREATE SESSION (FIRST — CRITICAL)
        # --------------------------------------------------
        session = ChatSession()
        db.add(session)
        db.flush()   # generates UUID

        # --------------------------------------------------
        # 2️⃣ CREATE AI DOCUMENT (WITH SESSION)
        # --------------------------------------------------
        aiDocument = (
            db.query(AIDocument)
            .filter(AIDocument.document_id == documentId)
            .first()
        )

        if not aiDocument:
            aiDocument = AIDocument(
                document_id=documentId,
                session_id=session.session_id,   # 🔥 NOT NULL
                filename=filename,
                file_type=fileType,
                file_size_mb=fileSizeMb,
            )
            db.add(aiDocument)
            db.commit()
        else:
            # Safety: should never happen, but keep system stable
            session.session_id = aiDocument.session_id

        # --------------------------------------------------
        # 3️⃣ EXTRACT + CHUNK FILE
        # --------------------------------------------------
        for pageNumber, rawText, usedOcr in iterateFilePages(filePath):
            if not rawText or not rawText.strip():
                continue

            ocrUsed = ocrUsed or usedOcr

            for chunk in chunkText(rawText):
                db.add(
                    DocumentChunk(
                        id=uuid.uuid4(),
                        document_id=documentId,
                        session_id=aiDocument.session_id,  # 🔒 SAME SESSION
                        chunk_index=chunksCreated,
                        chunk_text=chunk,
                        page_number=pageNumber,
                    )
                )
                chunksCreated += 1

        db.commit()

        return {
            "chunks": chunksCreated,
            "ocr_used": ocrUsed,
        }

    except Exception:
        db.rollback()
        logger.exception("Document ingestion failed")
        raise

    finally:
        db.close()


# ======================================================
# DOCUMENT SAVE / SUBMIT
# ======================================================

def saveDocument(
    db: Session,
    *,
    documentId: int,
    payload: DocumentSaveSchema,
    currentUser: dict,
):
    document = (
        db.query(Document)
        .filter(
            Document.id == documentId,
            Document.is_delete.is_(False),
        )
        .first()
    )

    if not document:
        raise HTTPException(404, "Document not found")

    if document.uploaded_by != currentUser["user_id"]:
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
        "documentId": document.id,
        "status": document.status,
    }


# ======================================================
# CREATE DOCUMENT DRAFT (BUSINESS FLOW)
# ======================================================

def createDocumentDraft(
    db: Session,
    *,
    aidocumentId: int,
    tempFilePath: str,
    originalFilename: str,
    departmentId: int,
    currentUser: dict,
):
    company = (
        db.query(Company)
        .filter(
            Company.id == currentUser["company_id"],
            Company.is_delete.is_(False),
        )
        .first()
    )

    if not company:
        raise HTTPException(404, "Company not found")

    document = Document(
        company_id=company.id,
        department_id=departmentId,
        uploaded_by=currentUser["user_id"],
        status="DRAFT",
    )
    db.add(document)
    db.flush()

    docDir = os.path.join(
        BASE_STORAGE_PATH,
        "companies",
        str(company.id),
        "documents",
        str(document.id),
    )
    os.makedirs(docDir, exist_ok=True)

    permanentPath = os.path.join(
        docDir,
        f"v1_{originalFilename}",
    )

    shutil.move(tempFilePath, permanentPath)

    fileSizeBytes = os.path.getsize(permanentPath)

    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        file_path=permanentPath,
        file_name=originalFilename,
        file_size_bytes=fileSizeBytes,
        ai_document_id=aidocumentId,
        created_by=currentUser["user_id"],
    )

    db.add(version)
    company.remaining_space -= fileSizeBytes
    db.commit()

    return document.id
