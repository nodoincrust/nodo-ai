import uuid
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.db import SessionLocal
import shutil
import os
from app.models import (
    AIDocument,
    DocuementChunks,
    DocumentVersion,
    DocumentReview,
    Document,
    Company,
    DocumentApprovalStep,
)
from app.AIhelpers.pdf_helper import extract_pdf_text
from app.AIhelpers.chunk_helper import chunk_text
from app.AIhelpers.embedding_helper import create_embedding
from app.schemas import DocumentSaveSchema
from app.helpers import resolve_hierarchy

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
        doc = AIDocument(
            document_id=document_id,
            session_id=session_id,
            filename=filename,
            file_type=file_type,
            file_size_mb=file_size_mb,
        )
        db.add(doc)
        db.commit()

        text, ocr_used = extract_pdf_text(file_path)

        chunks = chunk_text(text)

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


def save_document_draft(
    db: Session,
    document_id: int,
    payload: DocumentSaveSchema,
    current_user: dict,
):
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.uploaded_by == current_user["user_id"],
            Document.is_delete.is_(False),
        )
        .first()
    )

    if not document:
        raise HTTPException(404, "Document not found")

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

    db.commit()

    return {
        "document_id": document.id,
        "status": document.status,
        "summary": version.summary,
        "tags": version.tags,
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

    permanent_path = os.path.join(doc_dir, f"v1_{original_filename}")

    shutil.move(temp_file_path, permanent_path)
    document.current_file_path = permanent_path

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


def assign_document(
    db: Session,
    document_id: int,
    assign_level: str,
    current_user: dict,
):
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.uploaded_by == current_user["user_id"],
            Document.is_delete.is_(False),
        )
        .first()
    )

    if not document:
        raise HTTPException(404, "Document not found")

    dept_head, company_head = resolve_hierarchy(db, current_user)

    candidates: list[tuple[User, str]] = []

    if assign_level == "DEPARTMENT_HEAD":
        if not dept_head:
            raise HTTPException(400, "Department head not assigned")

        candidates.append((dept_head, "DEPARTMENT_HEAD"))

    elif assign_level == "COMPANY_ADMIN":
        if not dept_head:
            raise HTTPException(400, "Department head not assigned")
        if not company_head:
            raise HTTPException(400, "Company admin not found")

        candidates.append((dept_head, "DEPARTMENT_HEAD"))
        candidates.append((company_head, "COMPANY_ADMIN"))

    else:
        raise HTTPException(400, "Invalid assign level")

    approval_chain: list[tuple[User, str]] = []
    seen = set()

    for user, approver_type in candidates:
        if user.id not in seen:
            approval_chain.append((user, approver_type))
            seen.add(user.id)

    if not approval_chain:
        raise HTTPException(400, "No approvers found")

    db.query(DocumentApprovalStep).filter(
        DocumentApprovalStep.document_id == document.id
    ).delete(synchronize_session=False)

    for idx, (user, approver_type) in enumerate(approval_chain, start=1):
        db.add(
            DocumentApprovalStep(
                document_id=document.id,
                step_order=idx,
                assigned_to=user.id,
                approver_type=approver_type,
            )
        )

    # Update document tracking
    document.status = "IN_REVIEW"
    document.current_step_order = 1
    document.current_assignee_id = approval_chain[0][0].id

    db.commit()
