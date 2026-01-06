import logging
import json
import re
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    DocumentChunk,
    DocumentSummary,
    AIDocument,
)
from app.AIhelpers.llm_helper import askLlm

logger = logging.getLogger("ai.summaryService")

# ==============================
# CONFIG
# ==============================

SUMMARY_TOP_K = 15
MAX_CONTEXT_CHARS = 12000

BASE_SYSTEM_PROMPT = """
You are an enterprise document intelligence system. 

STRICT RULES:
- Return ONLY valid JSON.
- NO markdown code blocks.
- The summary MUST be detailed, between 15 to 20 lines in length excluding the tags and citation.

FORMATTING RULES:
1. Start with a comprehensive 3-5 sentence overview.
2. Provide a section titled "Detailed Breakdown" with specific points.
3. Provide a section titled "Key Insights & Implications".
4. Citations MUST ONLY contain the page_number (no excerpts).

OUTPUT FORMAT:
{
  "summary": "Overview text...\\n\\nDetailed Breakdown\\n- Point 1...\\n- Point 2...\\n\\nKey Insights & Implications\\n- Insight 1...",
  "tags": ["tag1", "tag2"],
  "citations": [{"page_number": 1}]
}
"""

REFINEMENT_PROMPT = """
You are an expert editor. You are provided with a DOCUMENT and a PREVIOUS SUMMARY.

TASK:
- Refine and expand the previous summary.
- Integrate new details found in the document context.
- Ensure the final output is 15-20 lines long excluding the tags and citation.
- Improve clarity and professional tone.
- Keep the same JSON structure.
"""

# ==============================
# SAFE JSON PARSER
# ==============================

def safeJsonParse(raw: str) -> dict:
    if not raw:
        return {"summary": "", "tags": [], "citations": []}

    # remove control chars
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

# ==============================
# MAIN SUMMARY SERVICE
# ==============================

def summarizeDocument(documentId: int) -> dict:
    """
    Generate or refine summary for a document.

    Rules:
    - Uses ai_documents.id internally
    - One LLM call per request
    - Idempotent (updates existing summary)
    """
    db: Session = SessionLocal()

    try:
        # --------------------------------------------------
        # 1️⃣ Resolve AI document (CRITICAL)
        # --------------------------------------------------
        aiDocument = (
            db.query(AIDocument)
            .filter(AIDocument.document_id == documentId)
            .first()
        )

        if not aiDocument:
            return {
                "status": "error",
                "message": "AI document not found",
            }

        # --------------------------------------------------
        # 2️⃣ Fetch existing summary (if any)
        # --------------------------------------------------
        existingSummary = (
            db.query(DocumentSummary)
            .filter_by(ai_document_id=aiDocument.id)
            .first()
        )

        # --------------------------------------------------
        # 3️⃣ Fetch chunks for summarization
        # --------------------------------------------------
        chunks = (
            db.query(DocumentChunk)
            .filter_by(ai_document_id=aiDocument.id)
            .order_by(DocumentChunk.chunk_index)
            .limit(SUMMARY_TOP_K)
            .all()
        )

        if not chunks:
            return {
                "status": "error",
                "message": "No content found for summarization",
            }

        documentContext = "\n\n".join(
            f"[PAGE {c.page_number}] {c.chunk_text}"
            for c in chunks
        )[:MAX_CONTEXT_CHARS]

        # --------------------------------------------------
        # 4️⃣ Decide prompt mode
        # --------------------------------------------------
        if existingSummary and existingSummary.summary_text:
            systemPrompt = BASE_SYSTEM_PROMPT + "\n" + REFINEMENT_PROMPT
            userPrompt = (
                f"DOCUMENT:\n{documentContext}\n\n"
                f"PREVIOUS SUMMARY:\n{existingSummary.summary_text}"
            )
            refined = True
        else:
            systemPrompt = BASE_SYSTEM_PROMPT
            userPrompt = f"DOCUMENT:\n{documentContext}"
            refined = False

        # --------------------------------------------------
        # 5️⃣ LLM call
        # --------------------------------------------------
        llmResult = askLlm(
            context=systemPrompt,
            question=userPrompt,
        )

        parsed = safeJsonParse(llmResult["data"]["answer"])

        # --------------------------------------------------
        # 6️⃣ Persist summary
        # --------------------------------------------------
        if existingSummary:
            existingSummary.summary_text = parsed["summary"]
            existingSummary.tags = parsed["tags"]
            existingSummary.citations = parsed["citations"]
        else:
            db.add(
                DocumentSummary(
                    ai_document_id=aiDocument.id,
                    summary_text=parsed["summary"],
                    tags=parsed["tags"],
                    citations=parsed["citations"],
                )
            )

        db.commit()

        # --------------------------------------------------
        # 7️⃣ Response
        # --------------------------------------------------
        return {
            "status": "success",
            "refined": refined,
            "summary": parsed["summary"],
            "tags": parsed["tags"],
            "citations": parsed["citations"],
        }

    except Exception as exc:
        db.rollback()
        logger.exception("Summary generation failed")
        return {
            "status": "error",
            "message": str(exc),
        }

    finally:
        db.close()
