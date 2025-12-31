import uuid
import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Document, DocuementChunks
from app.AIhelpers.pdf_helper import iter_pdf_pages, extract_pdf_text
from app.AIhelpers.format_helper import iter_file_pages
from app.AIhelpers.chunk_helper import chunk_text, chunk_text_from_pages
from app.AIhelpers.embedding_helper import create_embeddings, create_embedding, REDIS

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
    """
    Full document ingestion pipeline
    """
    db: Session = SessionLocal()
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

        logger.info("Starting ingestion for %s file=%s size=%.2fMB", document_id, filename, file_size_mb)

        #Stream-extract pages (OCR fallback per-file) to support multiple formats
        ocr_used = False

        def page_texts():
            nonlocal ocr_used
            for page_no, (p_text, p_ocr) in enumerate(
                iter_file_pages(file_path, file_type=file_type),
                start=1
            ):
                if p_ocr:
                    ocr_used = True
                yield page_no, p_text


        #Chunk text while streaming pages
        chunk_generator = chunk_text_from_pages(page_texts(), with_page=True)

        batch_size = 48
        objects_batch = []
        texts_batch = []
        idx = 0

        for chunk in chunk_generator:
            texts_batch.append(chunk["text"])

            # when batch ready, create embeddings and flush to DB
            if len(texts_batch) >= batch_size:
                try:
                    embeddings = create_embeddings(texts_batch)
                except Exception as e:
                    logger.exception("Embedding service failed for %s: %s", document_id, e)
                    embeddings = [None] * len(texts_batch)

                for emb_idx, emb in enumerate(embeddings):
                    objects_batch.append(
                        DocuementChunks(
                            id=uuid.uuid4(),
                            document_id=document_id,
                            session_id=session_id,
                            chunk_index=idx,
                            chunk_text=texts_batch[emb_idx],
                            embedding=emb,
                        )
                    )
                    idx += 1

                db.bulk_save_objects(objects_batch)
                db.commit()
                # progress update for this embedding batch
                try:
                    REDIS.setex(f"progress:{document_id}:embedding_batch", 86400, f"embedding_batch_done:{idx}")
                except Exception:
                    pass
                logger.info("Embedding batch done for %s: %s chunks processed", document_id, idx)
                objects_batch = []
                texts_batch = []

        # process remaining
        if texts_batch:
            try:
                embeddings = create_embeddings(texts_batch)
            except Exception as e:
                logger.exception("Embedding service failed for final batch %s: %s", document_id, e)
                embeddings = [None] * len(texts_batch)

            for emb_idx, emb in enumerate(embeddings):
                objects_batch.append(
                    DocuementChunks(
                        id=uuid.uuid4(),
                        document_id=document_id,
                        session_id=session_id,
                        chunk_index=idx,
                        chunk_text=texts_batch[emb_idx]["text"],
                        page_number=texts_batch[emb_idx]["page"],
                        embedding=emb,
                    )
                )
                idx += 1

            db.bulk_save_objects(objects_batch)
            db.commit()
            try:
                REDIS.setex(f"progress:{document_id}:embedding_batch", 86400, f"embedding_batch_done:{idx}")
            except Exception:
                pass
            logger.info("Embedding final batch done for %s: %s chunks processed", document_id, idx)

        chunks_count = idx

        # Final progress messages
        try:
            REDIS.setex(f"progress:{document_id}:chunk", 86400, f"chunk done for {document_id}")
        except Exception:
            pass
        logger.info("Chunking complete for %s", document_id)

        try:
            REDIS.setex(f"progress:{document_id}:embedding", 86400, f"embedding is done for {document_id}")
        except Exception:
            pass
        logger.info("Embedding complete for %s", document_id)

        try:
            REDIS.setex(f"progress:{document_id}:status", 86400, f"ingestion complete for {document_id}")
        except Exception:
            pass
        logger.info("Ingestion complete for %s", document_id)

        return {
            "status": "success",
            "chunks": chunks_count,
            "ocr_used": ocr_used,
            "document_id": document_id,
        }

    finally:
        db.close()
