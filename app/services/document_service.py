import logging
import shutil
import os
from typing import Dict, List
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.db import SessionLocal
from app.models import (
    Document,
    AIDocument,
    ChatSession,
    DocumentVersion,
    DocumentReview,
    Company,
)
from app.AIhelpers.chunk_helper import createDocumentChunks
from app.AIhelpers.format_helper import iterateFilePages
from app.schemas import DocumentSaveSchema

BASE_STORAGE_PATH = "storage"
logger = logging.getLogger(__name__)

MAX_UPLOAD_MB = 50
CHUNK_BATCH_SIZE = 32

#Core document processing function
def processDocument(
    *,
    filePath: str,
    document_id: int,     # documents.id
    filename: str,
    fileType: str,
    fileSizeMb: float,
) -> Dict:

    db: Session = SessionLocal()
    ocrUsed = False

    try:
        #Checks if AI metadata already exists
        ai_doc = (
            db.query(AIDocument)
            .filter(AIDocument.document_id == document_id)
            .first()
        )

        if not ai_doc:
            session = ChatSession() #creates new chat session for document
            db.add(session)
            db.flush()

            #maping with ai_document record
            ai_doc = AIDocument(
                document_id=document_id,
                session_id=session.session_id,
                filename=filename,
                file_type=fileType,
                file_size_mb=fileSizeMb,
            )
            db.add(ai_doc)
            db.commit()

        pages: List[tuple] = []

        #iterates through file pages to extract text (with OCR if needed)
        for pageNumber, rawText, usedOcr in iterateFilePages(filePath):  
            if not rawText or not rawText.strip():
                continue

            ocrUsed |= usedOcr   #flag if any page used OCR
            pages.append((pageNumber, rawText))

        if not pages:
            return {
                "status": "processing",
                "message": "No readable text extracted",
            }
        #Splits text into chunks, generates embeddings, and stores
        chunksCreated = createDocumentChunks(
            db=db,
            ai_document_id=ai_doc.id,          
            session_id=str(ai_doc.session_id),
            pages=pages,
        )

        return {
            "status": "success",
            "document_id": document_id,
            "session_id": str(ai_doc.session_id),
            "chunks": chunksCreated,
            "ocr_used": ocrUsed,
            "file_size_mb": round(fileSizeMb, 2),
        }

    except Exception as exc:
        db.rollback()
        logger.exception("Document ingestion failed")
        raise exc

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
