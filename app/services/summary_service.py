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
from .TagServices import select_top_chunks

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


def summarizeDocument(documentId: int, version: int, force_refine: bool = False) -> Dict[str, Any]:
    db: Session = SessionLocal()
    try:
        version_id = version
        
        logger.info(f"=== Starting summarizeDocument for documentId={documentId}, version_id={version_id} ===")
        
        # Get AI document for this exact version
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

        # Check for existing chunks
        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.ai_document_id == ai_doc.id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )

        logger.info(f"Found {len(chunks)} chunks for ai_document_id={ai_doc.id}")

        if not chunks:
            logger.info(f"No chunks found - triggering chunk creation")

            # Ensure session exists for the AIDocument
            try:
                session_id = getOrCreateSessionForDocument(documentId, version_id)
                logger.info(f"Ensured session exists: {session_id}")
            except Exception as e:
                logger.error(f"Failed to ensure session exists: {e}")
                return {
                    "status": "error",
                    "message": f"Failed to setup session: {str(e)}"
                }

            # Get the file path from DocumentVersion
            doc_version = db.query(DocumentVersion).filter(DocumentVersion.id == version_id).first()
            if not doc_version:
                logger.error(f"DocumentVersion not found for version_id={version_id}")
                return {
                    "status": "error",
                    "message": f"DocumentVersion not found for version_id={version_id}"
                }

            # Use the new function to create chunks without session conflicts
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
                logger.warning(f"Chunk creation failed or in progress")
                return {
                    "status": "processing",
                    "message": "Chunk creation in progress or failed",
                    "detail": process_result
                }

            # Reload chunks after creation
            chunks = (
                db.query(DocumentChunk)
                .filter(DocumentChunk.ai_document_id == ai_doc.id)
                .order_by(DocumentChunk.chunk_index)
                .all()
            )

            if not chunks:
                logger.error("Failed to create chunks after attempt")
                return {"status": "error", "message": "Failed to create chunks after attempt"}

        if len(chunks) > SUMMARY_TOP_K:
            logger.info(f"Selecting top {SUMMARY_TOP_K} chunks from {len(chunks)} total")
            chunks = select_top_chunks(db, chunks, "key content for document summary and tags", top_k=SUMMARY_TOP_K)

        context_parts = []
        default_citations = []

        for c in chunks:
            page_num = c.page_number if c.page_number else 1
            context_parts.append(f"[PAGE {page_num}] {c.chunk_text}")
            default_citations.append({"page_number": page_num})

        document_context = "\n\n".join(context_parts)[:MAX_CONTEXT_CHARS]
        logger.info(f"Prepared context: {len(document_context)} chars from {len(chunks)} chunks")

        existing = db.query(DocumentSummary).filter(
            DocumentSummary.ai_document_id == ai_doc.id,
            DocumentSummary.version_id == version_id
        ).first()

        logger.info(f"Existing summary found: {existing is not None}")

        system_prompt = BASE_SYSTEM_PROMPT + "\n" + TAG_GUIDANCE

        is_refinement = force_refine or (existing and existing.summary_text)

        if is_refinement:
            system_prompt += "\n" + REFINEMENT_PROMPT
            logger.info("Running in REFINEMENT mode")
        else:
            logger.info("Running in INITIAL GENERATION mode")

        llm_context = (
            f"{system_prompt}\n\n"
            f"PREVIOUS SUMMARY (if any):\n{existing.summary_text if existing else 'None'}\n\n"
            f"DOCUMENT EXCERPTS:\n{document_context}"
        )

        logger.info(f"Calling LLM with context length: {len(llm_context)}")

        llm_result = askLlm(
            context=llm_context,
            question="Generate or refine the document summary with tags and citations.",
        )

        logger.info(f"LLM result status: {llm_result.get('status')}")

        if llm_result.get("status") != "success":
            logger.error(f"LLM call failed: {llm_result}")
            return {
                "status": "error",
                "message": f"LLM call failed: {llm_result.get('data', {}).get('answer', 'Unknown error')}"
            }

        raw_answer = llm_result["data"]["answer"]
        logger.info(f"LLM raw answer (first 200 chars): {raw_answer[:200]}")

        parsed = safeJsonParse(raw_answer)

        if not parsed["summary"]:
            logger.error("Summary generation failed - no valid summary returned")
            return {"status": "error", "message": "Summary generation failed - no valid summary returned"}

        logger.info(f"Parsed summary length: {len(parsed['summary'])}, tags count: {len(parsed.get('tags', []))}")

        tags = [
            t.title().strip()
            for t in parsed["tags"]
            if isinstance(t, str) and 2 <= len(t.strip()) <= 30][:10]

        if not tags:
            logger.warning("Tags missing; using keyword fallback")
            tags = _fallback_keywords(parsed["summary"])

        citations = parsed["citations"] or default_citations

        logger.info(f"Final tags: {tags}")

        # RAG refinement
        should_refine_with_rag = not is_refinement and (not existing or len(tags) < 5)

        if should_refine_with_rag:
            logger.info("Performing second-pass RAG refinement")
            try:
                rag = RAGHelper(db)
                retrieved = rag.query(parsed["summary"], top_k=4)

                if retrieved:
                    examples = '\n'.join([
                        f"Similar document summary: {r['summary']}\nTags: {', '.join(r['tags'])}"
                        for r in retrieved
                    ])

                    refinement_prompt = (
                        BASE_SYSTEM_PROMPT + "\n" + TAG_GUIDANCE + "\n" + REFINEMENT_PROMPT +
                        f"\n\nExamples from similar documents:\n{examples}\n\n"
                        f"PREVIOUS SUMMARY:\n{parsed['summary']}\n\n"
                        f"DOCUMENT EXCERPTS:\n{document_context}"
                    )

                    logger.info("Calling LLM for RAG refinement")
                    refinement_result = askLlm(
                        context=refinement_prompt,
                        question="Refine the summary, improve tags using similar document examples."
                    )

                    if refinement_result.get("status") == "success":
                        refined_parsed = safeJsonParse(refinement_result["data"]["answer"])

                        if refined_parsed["summary"]:
                            parsed["summary"] = refined_parsed["summary"]
                            parsed["tags"] = refined_parsed["tags"]
                            parsed["citations"] = refined_parsed["citations"] or citations

                            tags = [
                                t.title().strip()
                                for t in parsed["tags"]
                                if isinstance(t, str) and 2 <= len(t.strip()) <= 30
                            ][:10] or tags

                            logger.info("RAG refinement completed successfully")
            except Exception as rag_exc:
                logger.warning(f"RAG refinement failed (non-critical): {rag_exc}")

        # 9. Save/Update summary per version
        try:
            if existing:
                logger.info(f"Updating existing summary for ai_document_id={ai_doc.id}")
                existing.summary_text = parsed["summary"]
                existing.tags = tags
                existing.citations = citations
                existing.updated_at = datetime.now(timezone.utc)
            else:
                logger.info(f"Creating new summary for ai_document_id={ai_doc.id}")
                new_summary = DocumentSummary(
                    ai_document_id=ai_doc.id,
                    version_id=version_id,
                    summary_text=parsed["summary"],
                    tags=tags,
                    citations=citations,
                )
                db.add(new_summary)

            db.commit()
            logger.info("Summary committed to database")
        except IntegrityError as e:
            logger.warning(f"IntegrityError on summary save, attempting update: {e}")
            db.rollback()
            # Try to update existing
            existing = db.query(DocumentSummary).filter(
                DocumentSummary.ai_document_id == ai_doc.id,
                DocumentSummary.version_id == version_id
            ).first()
            if existing:
                existing.summary_text = parsed["summary"]
                existing.tags = tags
                existing.citations = citations
                existing.updated_at = datetime.now(timezone.utc)
                db.commit()
                logger.info("Summary updated after IntegrityError")
            else:
                logger.error("Could not find or create summary after IntegrityError")
                raise

        # Update vector embedding
        try:
            logger.info("Updating summary embedding")
            rag = RAGHelper(db)
            rag.update_summary_embedding(ai_doc.id, parsed["summary"])
            logger.info("Embedding updated successfully")
        except Exception as emb_exc:
            logger.warning(f"Embedding update failed (non-critical): {emb_exc}")

        result = {
            "status": "success",
            "refined": is_refinement or should_refine_with_rag,
            "summary": parsed["summary"],
            "tags": tags,
            "citations": citations,
            "used_rag_refinement": should_refine_with_rag,
            "version_id": version_id
        }

        logger.info(f"=== Summary generation completed successfully ===")
        return result

    except Exception as exc:
        logger.exception(f"Summary generation failed with exception: {exc}")
        db.rollback()
        return {"status": "error", "message": str(exc)}

    finally:
        db.close()