# summary_service.py (full function)

import logging
import json
import re
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException

from app.db import SessionLocal
from app.models import DocumentChunk, DocumentSummary, AIDocument
from app.AIhelpers.llm_helper import askLlm, RAGHelper
from .TagServices import select_top_chunks, generateTagsFromLLM, storeDocumentTags

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


def summarizeDocument(documentId: int, version_id: int, force_refine: bool = False) -> Dict[str, Any]:
    """
    Generate or update summary, tags, and citations for a specific document version.
    Fully version-aware: uses version_id to fetch/store everything correctly.
    """
    db: Session = SessionLocal()
    try:
        # 1. Get the AI document for this exact version
        ai_doc = db.query(AIDocument).filter(
            AIDocument.document_id == documentId,
            AIDocument.version_id == version_id
        ).first()

        if not ai_doc:
            return {
                "status": "processing",
                "message": f"AIDocument not ready for document {documentId}, version {version_id}"
            }

        # 2. Check for existing chunks
        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.ai_document_id == ai_doc.id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )

        if not chunks:
            logger.info(f"No chunks found for document {documentId} version {version_id} — triggering chunk creation")

            from app.services.document_service import processDocument  # late import

            process_result = processDocument(
                document_id=documentId,
                versionId=version_id,  # Use versionId param
                filename=ai_doc.filename or "unknown",
                fileType=ai_doc.file_type or "pdf",
                fileSizeMb=ai_doc.file_size_mb or 0.0,
            )

            if process_result.get("status") not in ("success", "already_processed"):
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
                return {"status": "error", "message": "Failed to create chunks after attempt"}

        # 3. Limit context size for LLM
        if len(chunks) > SUMMARY_TOP_K:
            chunks = select_top_chunks(db, chunks, "key content for document summary and tags", top_k=SUMMARY_TOP_K)

        context_parts = []
        default_citations = []

        for c in chunks:
            context_parts.append(f"[PAGE {c.page_number}] {c.chunk_text}")
            default_citations.append({"page_number": c.page_number})

        document_context = "\n\n".join(context_parts)[:MAX_CONTEXT_CHARS]

        # 4. Check existing summary for this version
        existing = db.query(DocumentSummary).filter(
            DocumentSummary.ai_document_id == ai_doc.id,
            DocumentSummary.version_id == version_id
        ).first()

        # 5. Prepare prompt
        system_prompt = BASE_SYSTEM_PROMPT + "\n" + TAG_GUIDANCE

        is_refinement = force_refine or (existing and existing.summary_text)

        if is_refinement:
            system_prompt += "\n" + REFINEMENT_PROMPT

        llm_context = (
            f"{system_prompt}\n\n"
            f"PREVIOUS SUMMARY (if any):\n{existing.summary_text if existing else 'None'}\n\n"
            f"DOCUMENT EXCERPTS:\n{document_context}"
        )

        # 6. Call LLM
        llm_result = askLlm(
            context=llm_context,
            question="Generate or refine the document summary with tags and citations.",
        )

        parsed = safeJsonParse(llm_result["data"]["answer"])

        if not parsed["summary"]:
            return {"status": "error", "message": "Summary generation failed - no valid summary returned"}

        # 7. Clean and limit tags
        tags = [
            t.title().strip()
            for t in parsed["tags"]
            if isinstance(t, str) and 2 <= len(t.strip()) <= 30
        ][:10]

        if not tags:
            logger.warning("Tags missing; using keyword fallback.")
            tags = _fallback_keywords(parsed["summary"])

        citations = parsed["citations"] or default_citations

        # 8. Optional RAG refinement (second pass)
        should_refine_with_rag = not is_refinement and (not existing or len(tags) < 5)

        if should_refine_with_rag:
            logger.info("Performing second-pass RAG refinement")
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

                refinement_result = askLlm(
                    context=refinement_prompt,
                    question="Refine the summary, improve tags using similar document examples."
                )

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

        # 9. Save/Update summary per version
        if existing:
            existing.summary_text = parsed["summary"]
            existing.tags = tags
            existing.citations = citations
            existing.updated_at = func.now()
        else:
            new_summary = DocumentSummary(
                ai_document_id=ai_doc.id,
                version_id=version_id,           # ← Stored per version!
                summary_text=parsed["summary"],
                tags=tags,
                citations=citations,
            )
            db.add(new_summary)

        db.commit()

        # 10. Update vector embedding (for RAG search)
        rag = RAGHelper(db)
        rag.update_summary_embedding(ai_doc.id, parsed["summary"])

        return {
            "status": "success",
            "refined": is_refinement or should_refine_with_rag,
            "summary": parsed["summary"],
            "tags": tags,
            "citations": citations,
            "used_rag_refinement": should_refine_with_rag,
            "version_id": version_id
        }

    except Exception as exc:
        logger.exception("Summary generation failed")
        db.rollback()
        return {"status": "error", "message": str(exc)}

    finally:
        db.close()