import uuid
from typing import Optional
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Document, DocuementChunks
from app.AIhelpers.pdf_helper import extract_pdf_text
from app.AIhelpers.chunk_helper import chunk_text
from app.AIhelpers.embedding_helper import create_embedding


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
        doc = Document(
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
