import logging
import shutil
import os
# import uuid
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
    DocumentSummary,
)
from app.AIhelpers.chunk_helper import createDocumentChunks
from app.AIhelpers.format_helper import iterateFilePages
from app.schemas import DocumentSaveSchema

BASE_STORAGE_PATH = "storage"
logger = logging.getLogger(__name__)

MAX_UPLOAD_MB = 50
CHUNK_BATCH_SIZE = 32


def processDocument(
    *,
    document_id: int,
    filename: str,
    fileType: str,
    fileSizeMb: float,
) -> Dict:

    db: Session = SessionLocal()
    ocrUsed = False
    chunksCreated = 0

    try:
        version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
            .first()
        )

        if not version or not version.file_path:
            raise FileNotFoundError(
                f"No stored file found for document_id={document_id}"
            )

        storedFilePath = version.file_path

        if not os.path.exists(storedFilePath):
            raise FileNotFoundError(
                f"Stored document file missing: {storedFilePath}"
            )

        aiDocument = (
            db.query(AIDocument)
            .filter(AIDocument.document_id == document_id)
            .first()
        )

        if not aiDocument:
            session = ChatSession()
            db.add(session)
            db.flush()

            aiDocument = AIDocument(
                document_id=document_id,
                session_id=session.session_id,
                filename=filename,
                file_type=fileType,
                file_size_mb=fileSizeMb,
            )
            db.add(aiDocument)
            db.commit()

        lastChunkIndex = (
            db.query(DocumentChunk.chunk_index)
            .filter(DocumentChunk.ai_document_id == aiDocument.id)
            .order_by(DocumentChunk.chunk_index.desc())
            .limit(1)
            .scalar()
        )

        chunkIndex = (lastChunkIndex + 1) if lastChunkIndex is not None else 0

        for pageNumber, rawText, usedOcr in iterateFilePages(storedFilePath):
            if not rawText or not rawText.strip():
                continue

            ocrUsed |= usedOcr

            created = createDocumentChunks(
                db=db,
                ai_document_id=aiDocument.id,
                session_id=aiDocument.session_id,
                pages=[(pageNumber, rawText)],
                start_index=chunkIndex,   
            )

            chunkIndex += created
            chunksCreated += created

        db.commit()

        return {
            "status": "success",
            "document_id": document_id,
            "session_id": str(aiDocument.session_id),
            "chunks": chunksCreated,
            "ocr_used": ocrUsed,
            "file_size_mb": round(fileSizeMb, 2),
        }

    except Exception:
        db.rollback()
        logger.exception("Document ingestion failed")
        raise

    finally:
        db.close()

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
        raise HTTPException(400, "Only draft documents can be edited")

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

    ai_document = (
        db.query(AIDocument)
        .filter(AIDocument.document_id == document.id)
        .first()
    )

    if not ai_document:
        raise HTTPException(500, "AI document not found")

    if payload.summary is not None:
        ai_document.summary = DocumentSummary(
            summary_text=payload.summary,
            tags=payload.tags or [],
            citations=[],
        )

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
        "summary": version.summary,
        "tags": version.tags,
    }


def createDocumentDraft(
    db: Session,
    *,
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
        ai_document_id=None,
        created_by=currentUser["user_id"],
    )

    db.add(version)
    company.remaining_space -= fileSizeBytes
    db.commit()

    return document.id

def get_document_full_details(db: Session, document_id: int) -> dict:
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.is_delete.is_(False),
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    ai_document = (
        db.query(AIDocument)
        .filter(AIDocument.document_id == document.id)
        .first()
    )

    summary = None
    if ai_document:
        summary_record = (
            db.query(DocumentSummary)
            .filter(DocumentSummary.ai_document_id == ai_document.id)
            .first()
        )
        if summary_record:
            summary = {
                "summary": summary_record.summary_text,
                "tags": summary_record.tags,
                "citations": summary_record.citations,
            }

    return {
        "document_id": document.id,
        "status": document.status,
        "current_version": document.current_version,
        "created_at": document.created_at,
        "ai_ready": bool(ai_document),
        "session_id": str(ai_document.session_id) if ai_document else None,
        "summary": summary,
    }