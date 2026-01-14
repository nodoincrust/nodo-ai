import logging
import json
import re
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models import DocumentChunk, DocumentSummary, AIDocument
from app.AIhelpers.llm_helper import askLlm

logger = logging.getLogger("ai.summaryService")

SUMMARY_TOP_K = 12
MAX_CONTEXT_CHARS = 7000
jobs={}

BASE_SYSTEM_PROMPT = """
You are an enterprise document intelligence system.
STRICT RULES:
- Return ONLY valid JSON.
- NO markdown code blocks.
- The summary MUST be detailed, between 10 to 15 lines in length excluding the tags and citation.
 
FORMATTING RULES:
1. Start with a comprehensive 3-5 sentence overview.
2. Provide a section titled "Detailed Breakdown" with specific points.
3. Provide a section titled "Key Insights & Implications".
4. Citations MUST ONLY contain the page_number (no excerpts).
 
First plan the structure silently, then output the final JSON.
 
OUTPUT FORMAT (MANDATORY):
You MUST return ALL fields below.
If unsure, return empty arrays — NEVER omit fields.
 
{
  "summary": "string (required)",
  "tags": ["string", "string"] (required, may be empty),
  "citations": [{"page_number": number}] (required, may be empty)
}
"""

REFINEMENT_PROMPT = """
You are an expert editor. You are provided with a DOCUMENT and a PREVIOUS SUMMARY.
TASK:
Refine and expand the previous summary.
- Integrate new details found in the document context.
- Ensure the final output is 10-15 lines long excluding the tags and citation.
- Improve clarity and professional tone.
- Keep the same JSON structure.
"""


def safeJsonParse(raw: str) -> dict:
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
                data = json.loads(cleanRaw[start : end + 1], strict=False)
            except Exception:
                return {"summary": cleanRaw, "tags": [], "citations": []}
        else:
            return {"summary": cleanRaw, "tags": [], "citations": []}

    summaryText = data.get("summary", "")
    tags = list(set(map(str, data.get("tags", []))))
    citations = []

    seenPages = set()
    for c in data.get("citations", []):
        page = c.get("page_number") if isinstance(c, dict) else c
        if page and page not in seenPages:
            citations.append({"page_number": page})
            seenPages.add(page)

    return {
        "summary": summaryText.strip(),
        "tags": tags,
        "citations": citations,
    }

def summarizeDocument(documentId: int, version: int | None = None) -> dict:
    db: Session = SessionLocal()
    try:
        # Select ai document by version
        if version:
            ai_doc = db.query(AIDocument).filter(
                AIDocument.document_id == documentId,
                AIDocument.version_id == version
            ).first()
        else:
            # auto-latest
            ai_doc = db.query(AIDocument).filter(
                AIDocument.document_id == documentId
            ).order_by(AIDocument.version_id.desc()).first()

        if not ai_doc:
            return {
                "status": "processing",
                "message": "Document ingestion not completed yet"
            }

        ai_document_id = ai_doc.id

        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.ai_document_id == ai_document_id)
            .order_by(DocumentChunk.chunk_index)
            .limit(SUMMARY_TOP_K)
            .all()
        )

        if not chunks:
            return {
                "status": "processing",
                "message": "Chunks not ready yet"
            }

        # build context
        chunk_parts = []
        default_citations = []

        for chunk in chunks:
            chunk_parts.append(f"[PAGE {chunk.page_number}] {chunk.chunk_text}")
            default_citations.append({"page_number": chunk.page_number})

        document_context = "\n\n".join(chunk_parts)[:MAX_CONTEXT_CHARS]

        existing = db.query(DocumentSummary).filter(
            DocumentSummary.ai_document_id == ai_document_id
        ).first()

        previous_summary = existing.summary_text if existing else ""

        llm_context = (
            f"{BASE_SYSTEM_PROMPT}\n\n"
            f"PREVIOUS SUMMARY:\n{previous_summary}\n\n"
            f"DOCUMENT EXCERPTS:\n{document_context}"
        )

        llm_result = askLlm(
            context=llm_context,
            question="Regenerate the summary using the provided content.",
        )

        parsed = safeJsonParse(llm_result["data"]["answer"])
        summary_text = parsed.get("summary", "").strip()
        tags = parsed.get("tags", [])
        citations = parsed.get("citations", default_citations)

        if not summary_text:
            return {"status": "error", "message": "Summary generation failed"}

        # normalize tags
        tags = [
            str(t).strip().title()
            for t in tags
            if isinstance(t, str) and 2 <= len(t.strip()) <= 30
        ]
        tags = list(dict.fromkeys(tags))[:6]

        # write / update
        if existing:
            existing.summary_text = summary_text
            existing.tags = tags
            existing.citations = citations
        else:
            db.add(
                DocumentSummary(
                    ai_document_id=ai_document_id,
                    summary_text=summary_text,
                    tags=tags,
                    citations=citations,
                )
            )

        db.commit()

        return {
            "status": "success",
            "refined": True,
            "summary": summary_text,
            "tags": tags,
            "citations": citations,
        }

    finally:
        db.close()
