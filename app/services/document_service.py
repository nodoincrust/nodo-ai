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
    DocumentSummary,
)
from app.AIhelpers.chunk_helper import chunkText
from app.AIhelpers.format_helper import iterateFilePages
from app.schemas import DocumentSaveSchema

BASE_STORAGE_PATH = "storage"
logger = logging.getLogger(__name__)

MAX_UPLOAD_MB = 50
CHUNK_BATCH_SIZE = 32


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

        session = ChatSession()
        db.add(session)
        db.flush()  # generates UUID

        aiDocument = (
            db.query(AIDocument).filter(AIDocument.document_id == documentId).first()
        )

        if not aiDocument:
            aiDocument = AIDocument(
                document_id=documentId,
                session_id=session.session_id,
                filename=filename,
                file_type=fileType,
                file_size_mb=fileSizeMb,
            )
            db.add(aiDocument)
            db.commit()
        else:
            session.session_id = aiDocument.session_id

        for pageNumber, rawText, usedOcr in iterateFilePages(filePath):
            if not rawText or not rawText.strip():
                continue

            ocrUsed |= usedOcr

            for chunk in chunkText(rawText):
                db.add(
                    DocumentChunk(
                        id=uuid.uuid4(),
                        ai_document_id=aiDocument.id,
                        session_id=aiDocument.session_id,
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
            "session_id":str(aiDocument.session_id)
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

    return {
        "document_id": document.id,
        "file_path": permanentPath,
    }

def get_document_full_details(
    db: Session,
    *,
    document_id: int,
    current_user: dict,
):

    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.is_delete.is_(False),
            Document.company_id == current_user["company_id"],
        )
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    ai_document = (
        db.query(AIDocument)
        .filter(AIDocument.document_id == document.id)
        .first()
    )

    summary = ai_document.summary if ai_document else None

    review = (
        db.query(DocumentReview)
        .filter(DocumentReview.document_id == document.id)
        .order_by(DocumentReview.created_at.desc())
        .first()
    )

    return {
        "document": {
            "id": document.id,
            "status": document.status,
            "is_active": document.is_active,
            "created_at": document.created_at,
            "current_version": document.current_version,
            "uploaded_by": document.uploaded_by,
            "department_id": document.department_id,
            "company_id": document.company_id,
        },
        "file": {
            "file_name": version.file_name if version else None,
            "file_path": (
                "/" + version.file_path.replace("\\", "/").lstrip("/")
                if version
                else None
            ),
            "file_size_bytes": version.file_size_bytes if version else None,
            "version_number": version.version_number if version else None,
        },
        "ai": {
            "ai_document_id": ai_document.id if ai_document else None,
            "session_id": str(ai_document.session_id) if ai_document else None,
            "file_type": ai_document.file_type if ai_document else None,
            "file_size_mb": (
                float(ai_document.file_size_mb)
                if ai_document and ai_document.file_size_mb
                else None
            ),
        },
        "summary": {
            "text": summary.summary_text if summary else None,
            "tags": summary.tags or [] if summary else [],
            "citations": summary.citations or [] if summary else [],
        },
        "review": {
            "status": review.status if review else None,
            "reviewed_by": review.reviewed_by if review else None,
        },
    }
