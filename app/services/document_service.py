import uuid
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.db import SessionLocal
import shutil
import os
from app.models import AIDocument, DocuementChunks,DocumentVersion,DocumentReview,Document,Company
from app.AIhelpers.pdf_helper import extract_pdf_text
from app.AIhelpers.chunk_helper import chunk_text
from app.AIhelpers.embedding_helper import create_embedding
from app.schemas import DocumentSaveSchema
BASE_STORAGE_PATH = "storage"

def process_document(
    file_path: str,
    document_id: str,
    filename: str,
    session_id: Optional[str],
    file_type: str,
    file_size_mb: float,
) -> dict:
    """Full document ingestion pipeline"""
    db: Session = SessionLocal()
    try:
        # Store document metadata
        doc = AIDocument(
            document_id=document_id,
            session_id=session_id,
            filename=filename,
            file_type=file_type,
            file_size_mb=file_size_mb,
        )
        db.add(doc)
        db.commit()

        # Extract text (OCR fallback)
        text, ocr_used = extract_pdf_text(file_path)

        # Chunk text
        chunks = chunk_text(text)

        # Store chunks + embeddings (batched)
        objects = []
        for idx, chunk in enumerate(chunks):
            emb = create_embedding(chunk)
            objects.append(
                DocuementChunks(
                    id=uuid.uuid4(),
                    document_id=document_id,
                    session_id=session_id,
                    chunk_index=idx,
                    chunk_text=chunk,
                    embedding=emb,
                )
            )

        db.bulk_save_objects(objects)
        db.commit()

        return {
            "status": "success",
            "chunks": len(chunks),
            "ocr_used": ocr_used,
            "document_id": document_id,
        }

    finally:
        db.close()




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
        raise HTTPException(403, "You cannot save this document")

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
    

def create_document_draft(
    db: Session,
    *,
    ai_document_id: str,
    temp_file_path: str,
    original_filename: str,
    department_id: int,
    current_user: dict,
):
    # 1️⃣ Fetch company
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

    # 2️⃣ Create business document (DRAFT)
    document = Document(
        company_id=company.id,
        department_id=department_id,
        uploaded_by=current_user["user_id"],
        status="DRAFT",
    )
    db.add(document)
    db.flush()  # get document.id

    # 3️⃣ Permanent storage path
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
        f"v1_{original_filename}"
    )

    # 4️⃣ Move temp file → permanent storage
    shutil.move(temp_file_path, permanent_path)

    file_size_bytes = os.path.getsize(permanent_path)

    # 5️⃣ Create version v1
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

    # 6️⃣ Deduct storage
    company.remaining_space -= file_size_bytes

    db.commit()

    return document.id