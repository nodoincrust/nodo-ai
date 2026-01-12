import logging
import json
import re
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import DocumentChunk, DocumentSummary, AIDocument
from app.AIhelpers.llm_helper import askLlm

logger = logging.getLogger("ai.summaryService")

SUMMARY_TOP_K = 20                 # Limits chunks to control latency
MAX_CONTEXT_CHARS = 7000           # Prevents token overflow

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
Refine and expand the previous summary using new document context.
Keep the same JSON structure.
"""


def safeJsonParse(raw: str) -> dict:
    if not raw:
        return {"summary": "", "tags": [], "citations": []}  # Handles empty LLM response

    cleanRaw = re.sub(r"[\x00-\x1F\x7F]", "", raw)  # Removes invalid characters

    try:
        data = json.loads(cleanRaw, strict=False)  # Attempts strict JSON parsing
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
    tags = list(dict.fromkeys(map(str, data.get("tags", []))))  # Deduplicates tags
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


def summarizeDocument(documentId: int) -> dict:
    db: Session = SessionLocal()
    try:
        ai_doc = (
            db.query(AIDocument)
            .filter(AIDocument.document_id == documentId)
            .first()
        )

        if not ai_doc:
            return {"status": "processing", "message": "Document ingestion not completed"}  # Guards early calls

        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.ai_document_id == ai_doc.id)
            .order_by(DocumentChunk.chunk_index)
            .limit(SUMMARY_TOP_K)
            .all()
        )

        if not chunks:
            return {"status": "processing", "message": "Chunks not ready yet"}  # Handles async ingestion

        context_parts = []
        default_citations = []

        for c in chunks:
            context_parts.append(f"[PAGE {c.page_number}] {c.chunk_text}")
            default_citations.append({"page_number": c.page_number})

        document_context = "\n\n".join(context_parts)[:MAX_CONTEXT_CHARS]  # Trims context safely

        existing = (
            db.query(DocumentSummary)
            .filter(DocumentSummary.ai_document_id == ai_doc.id)
            .first()
        )

        system_prompt = BASE_SYSTEM_PROMPT
        if existing and existing.summary_text:
            system_prompt += "\n" + REFINEMENT_PROMPT  # Enables summary refinement

        llm_context = (
            f"{system_prompt}\n\n"
            f"PREVIOUS SUMMARY:\n{existing.summary_text if existing else ''}\n\n"
            f"DOCUMENT EXCERPTS:\n{document_context}"
        )

        llm_result = askLlm(
            context=llm_context,
            question="Generate or refine the document summary.",
        )  # Single LLM call only

        parsed = safeJsonParse(llm_result["data"]["answer"])  # Ensures stable output

        if not parsed["summary"]:
            return {"status": "error", "message": "Summary generation failed"}  # Hard failure guard

        tags = [
            t.title().strip()
            for t in parsed["tags"]
            if isinstance(t, str) and 2 <= len(t.strip()) <= 30
        ][:6]  # Normalizes and limits tags

        citations = parsed["citations"] or default_citations

        if existing:
            existing.summary_text = parsed["summary"]
            existing.tags = tags
            existing.citations = citations
        else:
            db.add(
                DocumentSummary(
                    ai_document_id=ai_doc.id,
                    summary_text=parsed["summary"],
                    tags=tags,
                    citations=citations,
                )
            )

        db.commit()

        return {
            "status": "success",
            "refined": bool(existing),
            "summary": parsed["summary"],
            "tags": tags,
            "citations": citations,
        }

    except Exception as exc:
        logger.exception("Summary generation failed")  # Logs unexpected failures
        return {"status": "error", "message": str(exc)}

    finally:
        db.close()  # Always releases DB session
