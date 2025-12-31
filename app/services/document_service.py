import uuid
import logging
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.db import SessionLocal
from app.models import Document, DocuementChunks,ChatSession
from app.AIhelpers.format_helper import iter_file_pages
from app.AIhelpers.chunk_helper import chunk_text_from_pages
from app.AIhelpers.embedding_helper import create_embeddings, REDIS

logger = logging.getLogger("ai_modul.document_service")

def validate_document(document_id: str):
    db = SessionLocal()
    try:
        exists = (
            db.query(Document)
            .filter(Document.document_id == document_id)
            .first()
        )
        if not exists:
            raise HTTPException(
                status_code=404,
                detail=f"Document {document_id} not found"
            )
    finally:
        db.close()

def process_document(
    file_path: str,
    document_id: str,
    filename: str,
    session_id: Optional[str],
    file_type: str,
    file_size_mb: float,
) -> dict:

    db: Session = SessionLocal()
    if session_id is None:
        session = ChatSession()
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.session_id
    try:
        #Store document metadata
        doc = Document(
            document_id=document_id,
            session_id=session_id,
            filename=filename,
            file_type=file_type,
            file_size_mb=file_size_mb,
        )
        db.add(doc)
        db.commit()

        logger.info(
            "Starting ingestion for %s file=%s size=%.2fMB",
            document_id, filename, file_size_mb
        )

        ocr_used = False

        def page_texts():
            nonlocal ocr_used
            for page_no, (text, used_ocr) in enumerate(
                iter_file_pages(file_path, file_type=file_type),
                start=1
            ):
                if used_ocr:
                    ocr_used = True
                yield page_no, text

        chunk_generator = chunk_text_from_pages(
            page_texts(),
            with_page=True
        )

        batch_size = 48

        texts_batch: list[str] = []
        page_numbers: list[Optional[int]] = []
        objects_batch = []

        idx = 0

        for raw_chunk in chunk_generator:

            # 🔒 Normalize ONCE
            if isinstance(raw_chunk, dict):
                text = raw_chunk.get("text")
                page = raw_chunk.get("page")
            else:
                text = raw_chunk
                page = None

            if not isinstance(text, str) or not text.strip():
                continue

            texts_batch.append(text)
            page_numbers.append(page)

            # Flush batch
            if len(texts_batch) >= batch_size:
                _flush_batch(
                    db=db,
                    document_id=document_id,
                    session_id=session_id,
                    texts=texts_batch,
                    pages=page_numbers,
                    start_index=idx,
                    objects_batch=objects_batch,
                )
                idx += len(texts_batch)

                texts_batch.clear()
                page_numbers.clear()
                objects_batch.clear()

        if texts_batch:
            _flush_batch(
                db=db,
                document_id=document_id,
                session_id=session_id,
                texts=texts_batch,
                pages=page_numbers,
                start_index=idx,
                objects_batch=objects_batch,
            )
            idx += len(texts_batch)
        try:
            REDIS.setex(
                f"progress:{document_id}:status",
                86400,
                f"ingestion complete for {document_id}"
            )
        except Exception:
            pass

        logger.info("Ingestion complete for %s (%s chunks)", document_id, idx)

        return {
            "status": "success",
            "document_id": document_id,
            "chunks": idx,
            "ocr_used": ocr_used,
        }

    finally:
        db.close()

def _flush_batch(
    *,
    db: Session,
    document_id: str,
    session_id: Optional[str],
    texts: list[str],
    pages: list[Optional[int]],
    start_index: int,
    objects_batch: list,
):
    assert len(texts) == len(pages)

    try:
        embeddings = create_embeddings(texts)
    except Exception as e:
        logger.exception(
            "Embedding service failed for %s: %s",
            document_id, e
        )
        embeddings = [None] * len(texts)

    for i, emb in enumerate(embeddings):
        objects_batch.append(
            DocuementChunks(
                id=uuid.uuid4(),
                document_id=document_id,
                session_id=session_id,
                chunk_index=start_index + i,
                chunk_text=texts[i],
                embedding=emb,
                page_number=pages[i],
            )
        )

    db.bulk_save_objects(objects_batch)
    db.commit()

    try:
        REDIS.setex(
            f"progress:{document_id}:embedding_batch",
            86400,
            f"embedding_batch_done:{start_index + len(texts)}"
        )
    except Exception:
        pass