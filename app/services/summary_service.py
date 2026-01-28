import logging
import json
import re
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import DocumentChunk, DocumentSummary, AIDocument, DocumentVersion
from app.AIhelpers.llm_helper import askLlm, RAGHelper
from app.services.ai_DBservice import getOrCreateSessionForDocument, createChunksForExistingAIDocument
from app.AIhelpers.chunk_helper import select_top_chunks

logger = logging.getLogger("ai.summaryService")

SUMMARY_TOP_K = 100
MAX_CONTEXT_CHARS = 30000

BASE_SYSTEM_PROMPT = """
You are an enterprise document intelligence system.
STRICT RULES:
 Return ONLY valid JSON.
 NO markdown code blocks.
 The summary MUST be written in FULL 2 PARAGRAPHS.
 Bullet points, hyphens, or numbered lists are STRICTLY FORBIDDEN.
 Each line MUST be a complete explanatory sentence.

OUTPUT FORMAT (MANDATORY):
{
  "summary": "string",
  "tags": ["string"],
  "citations": [{"page_number": number}]
}
"""

REFINEMENT_PROMPT = """
You are an expert editor.
Refine and expand the previous summary using new document context and similar document examples.
Improve tag relevance and completeness.
Keep the same JSON structure.
Make the summary more comprehensive and professional while staying faithful to the document.
"""

TAG_GUIDANCE = """
For tags, generate 5-8 concise tags (2-4 words each) that are *highly relevant* to the document's content.
Examples of good, relevant tags:
- "carbon footprint"
- "environmental impact"
- "product lifecycle data"
- "kgCO2eq metrics"
- "furniture products"
- "supplier emissions"
"""

def safeJsonParse(raw: str) -> Dict[str, Any]:
    if not raw:
        return {"summary": "", "tags": [], "citations": []}

    cleanRaw = re.sub(r"[\x00-\x1F\x7F]", "", raw)

    try:
        data = json.loads(cleanRaw, strict=False)
    except Exception:
        start = cleanRaw.find("{")
        end = cleanRaw.rfind("}")
        if start != -1 and end != -1:
            try:
                data = json.loads(cleanRaw[start:end + 1], strict=False)
            except Exception:
                return {"summary": cleanRaw, "tags": [], "citations": []}
        else:
            return {"summary": cleanRaw, "tags": [], "citations": []}

    summaryText = data.get("summary", "").strip()
    tags = list(dict.fromkeys(map(str, data.get("tags", []))))
    citations = []

    seen = set()
    for c in data.get("citations", []):
        page = c.get("page_number") if isinstance(c, dict) else c
        if page and page not in seen:
            citations.append({"page_number": page})
            seen.add(page)

    return {
        "summary": summaryText,
        "tags": tags,
        "citations": citations,
    }


def _fallback_keywords(text: str) -> List[str]:
    stop_words = {"the", "and", "is", "in", "to", "of", "a", "for", "on", "with", "as", "by", "that", "this", "are", "was", "it", "be", "or", "from", "at", "an", "which"}
    try:
        words = re.findall(r'\b\w+\b', text.lower())
        keywords = [w for w in words if w.isalpha() and w not in stop_words]
        return list(set(keywords))[:10]
    except Exception:
        return []

SUMMARY_SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + "\n" + TAG_GUIDANCE
def summarizeDocument(documentId: int, version: int, force_refine: bool = False) -> Dict[str, Any]:
    db: Session = SessionLocal()
    try:
        version_id = version
        
        logger.info(f"=== Starting summarizeDocument for documentId={documentId}, version_id={version_id} ===")
        
        # 1. Get AI document for this exact version
        ai_doc = db.query(AIDocument).filter(
            AIDocument.document_id == documentId,
            AIDocument.version_id == version_id
        ).first()

        if not ai_doc:
            logger.warning(f"AIDocument not found for document {documentId}, version {version_id}")
            return {
                "status": "processing",
                "message": f"AIDocument not ready for document {documentId}, version {version_id}"
            }

        logger.info(f"Found AIDocument: id={ai_doc.id}, session_id={ai_doc.session_id}")

        # 2. Load chunks
        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.ai_document_id == ai_doc.id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )

        logger.info(f"Found {len(chunks)} chunks for ai_document_id={ai_doc.id}")

        if not chunks:
            logger.info("No chunks found - triggering chunk creation")

            try:
                session_id = getOrCreateSessionForDocument(documentId, version_id)
                logger.info(f"Ensured session exists: {session_id}")
            except Exception as e:
                logger.error(f"Failed to ensure session exists: {e}")
                return {
                    "status": "error",
                    "message": f"Failed to setup session: {str(e)}"
                }

            doc_version = db.query(DocumentVersion).filter(
                DocumentVersion.id == version_id
            ).first()

            if not doc_version:
                logger.error(f"DocumentVersion not found for version_id={version_id}")
                return {
                    "status": "error",
                    "message": f"DocumentVersion not found for version_id={version_id}"
                }

            process_result = createChunksForExistingAIDocument(
                documentId=documentId,
                versionId=version_id,
                filePath=doc_version.file_path,
                filename=ai_doc.filename or "unknown",
                fileType=ai_doc.file_type or "pdf",
                fileSizeMb=float(ai_doc.file_size_mb) if ai_doc.file_size_mb else 0.0,
            )

            logger.info(f"Process result: {process_result}")

            if process_result.get("status") not in ("success", "already_processed"):
                return {
                    "status": "processing",
                    "message": "Chunk creation in progress or failed",
                    "detail": process_result
                }

            chunks = (
                db.query(DocumentChunk)
                .filter(DocumentChunk.ai_document_id == ai_doc.id)
                .order_by(DocumentChunk.chunk_index)
                .all()
            )

            if not chunks:
                logger.error("Failed to create chunks after attempt")
                return {
                    "status": "error",
                    "refined": False,
                    "summary": (
                        "No readable textual content could be extracted from this document. "
                        "It may contain only images, UI screenshots, or non-textual data."
                    ),
                    "tags": [],
                    "version_id": version_id,
                }

        valid_chunks = []

        for c in chunks:
            text = (c.chunk_text or "").strip()

            # Ignore ingestion / OCR error markers
            if text.startswith("[INGESTION ERROR]"):
                continue
            if text.startswith("[PDF PAGE ERROR]"):
                continue
            if text.startswith("[IMAGE ERROR]"):
                continue
            if text.startswith("[EXCEL ERROR]"):
                continue

            # Require minimum semantic signal
            alpha_count = sum(ch.isalpha() for ch in text)
            if alpha_count < 40:
                continue

            valid_chunks.append(c)

        logger.info(f"Valid chunks after filtering: {len(valid_chunks)}")

        # HARD STOP — prevent hallucination
        if not valid_chunks:
            logger.error("Chunks exist but contain no meaningful semantic content")
            return {
                "status": "error",
                "refined": False,
                "summary": (
                    "The document does not contain sufficient readable or meaningful text "
                    "to generate a reliable summary. It may consist mainly of images, "
                    "IDs, metadata, or non-descriptive tables."
                ),
                "tags": [],
                "version_id": version_id,
            }

        # 5. Select top chunks if needed
        if len(valid_chunks) > SUMMARY_TOP_K:
            valid_chunks = select_top_chunks(
                db,
                valid_chunks,
                "key content for document summary and tags",
                top_k=SUMMARY_TOP_K,
            )

        # 6. Build context
        context_parts = []
        default_citations = []

        for c in valid_chunks:
            page_num = c.page_number if c.page_number else 1
            context_parts.append(f"[PAGE {page_num}] {c.chunk_text}")
            default_citations.append({"page_number": page_num})

        document_context = "\n\n".join(context_parts)[:MAX_CONTEXT_CHARS]

        logger.info(f"Prepared context: {len(document_context)} chars")

        is_ocr_doc = any(
            c.chunk_text.startswith((
                "[IMAGE OCR]",
                "[OCR PAGE]",
                "[SLIDE IMAGE OCR]",
            ))
            for c in valid_chunks
        )

        min_len = 1 if is_ocr_doc else 300

        if len(document_context) < min_len:
            return {
                "status": "error",
                "refined": False,
                "summary": (
                    "Extracted text is too limited to produce a reliable summary."
                ),
                "tags": [],
                "version_id": version_id,
            }

        # 7. Existing summary
        existing = db.query(DocumentSummary).filter(
            DocumentSummary.ai_document_id == ai_doc.id,
            DocumentSummary.version_id == version_id
        ).first()

        is_refinement = force_refine or (existing and existing.summary_text)

        system_prompt = SUMMARY_SYSTEM_PROMPT
        if is_refinement:
            system_prompt += "\n" + REFINEMENT_PROMPT

        llm_context = (
            f"PREVIOUS SUMMARY (if any):\n{existing.summary_text if existing else 'None'}\n\n"
            f"DOCUMENT EXCERPTS:\n{document_context}"
        )

        llm_result = askLlm(
            context=llm_context,
            question="Generate or refine the document summary with tags and citations.",
            system_prompt=system_prompt,
        )

        if llm_result.get("status") != "success":
            return {
                "status": "error",
                "message": "LLM call failed"
            }

        parsed = safeJsonParse(llm_result["data"]["answer"])

        if not parsed["summary"]:
            return {
                "status": "error",
                "message": "Summary generation failed - empty summary"
            }

        tags = [
            t.title().strip()
            for t in parsed.get("tags", [])
            if isinstance(t, str) and 2 <= len(t.strip()) <= 30
        ][:10]

        if not tags:
            tags = _fallback_keywords(parsed["summary"])

        citations = parsed.get("citations") or default_citations

        # 8. Save summary
        try:
            if existing:
                existing.summary_text = parsed["summary"]
                existing.tags = tags
                existing.citations = citations
                existing.updated_at = datetime.now(timezone.utc)
            else:
                db.add(
                    DocumentSummary(
                        ai_document_id=ai_doc.id,
                        version_id=version_id,
                        summary_text=parsed["summary"],
                        tags=tags,
                        citations=citations,
                    )
                )
            db.commit()

        except IntegrityError:
            db.rollback()
            # HARD SAFE UPDATE
            existing = db.query(DocumentSummary).filter(
                DocumentSummary.ai_document_id == ai_doc.id,
                DocumentSummary.version_id == version_id,
            ).first()

            if existing:
                existing.summary_text = parsed["summary"]
                existing.tags = tags
                existing.citations = citations
                existing.updated_at = datetime.now(timezone.utc)
                db.commit()


        # 9. Update embedding (non-critical)
        try:
            rag = RAGHelper(db)
            rag.update_summary_embedding(ai_doc.id, parsed["summary"])
        except Exception as emb_exc:
            logger.warning(f"Embedding update failed: {emb_exc}")

        return {
            "status": "success",
            "refined": bool(is_refinement),
            "summary": parsed["summary"],
            "tags": tags,
            "version_id": version_id
        }

    except Exception as exc:
        logger.exception(f"Summary generation failed: {exc}")
        db.rollback()
        return {"status": "error", "message": str(exc)}

    finally:
        db.close()