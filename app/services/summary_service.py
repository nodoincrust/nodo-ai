# app/services/summary_service.py

from sqlalchemy.orm import Session
import logging
import json

from app.db import SessionLocal
from app.models import DocuementChunks, DocuemntSummery
from app.AIhelpers.llm_helper import ask_llm

logger = logging.getLogger("ai_modul.summary_service")

# ==============================
# CONFIG
# ==============================

SUMMARY_TOP_K = 8
MAX_CONTEXT_CHARS = 6000

BASE_SYSTEM_PROMPT = """
You are an enterprise document intelligence system.

STRICT RULES:
- You MUST return valid JSON only.
- NO text before or after JSON.
- NO markdown.
- NO explanations.

SUMMARY RULES:
- Summary MUST be 5 to 15 bullet lines.
- Each line must be a complete sentence.
- Capture purpose, scope, structure, and key insights.
- If the document is tabular, explain what the data represents.
- DO NOT repeat lines.
- DO NOT be vague.

OUTPUT FORMAT (STRICT JSON):
{
  "summary": "- line 1\\n- line 2\\n- line 3",
  "tags": ["tag1", "tag2"],
  "citations": [
    {
      "page_number": 1,
      "excerpt": "short relevant text"
    }
  ]
}
"""

REFINEMENT_INSTRUCTIONS = """
You are given:
1. The document content
2. A PREVIOUS SUMMARY

TASK:
Improve the previous summary using the document content.

IMPROVEMENT GUIDELINES:
- Fix unclear or weak lines
- Add missing important points
- Remove redundancy
- Keep length between 5–15 lines
- Do NOT invent facts
- Do NOT reduce quality

Return a BETTER version of the summary.
"""

# ==============================
# SAFE JSON PARSER
# ==============================

def safe_json_parse(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(raw[start:end + 1])
        else:
            return {
                "summary": raw[:1500],
                "tags": [],
                "citations": [],
            }

    # Handle nested JSON accidentally returned as string
    summary = data.get("summary", "")
    if isinstance(summary, str) and summary.strip().startswith("{"):
        inner = json.loads(summary)
        return {
            "summary": inner.get("summary", ""),
            "tags": inner.get("tags", []),
            "citations": inner.get("citations", []),
        }

    return {
        "summary": data.get("summary", ""),
        "tags": data.get("tags", []),
        "citations": data.get("citations", []),
    }

# ==============================
# MAIN API
# ==============================

def summarize_doc(document_id: str):
    db: Session = SessionLocal()

    try:
        # 1️⃣ Fetch previous summary (if any)
        existing = (
            db.query(DocuemntSummery)
            .filter_by(document_id=document_id)
            .first()
        )

        # 2️⃣ Fetch chunks
        chunks = (
            db.query(DocuementChunks)
            .filter_by(document_id=document_id)
            .order_by(DocuementChunks.chunk_index)
            .limit(SUMMARY_TOP_K)
            .all()
        )

        if not chunks:
            return {
                "status": "error",
                "message": "No document content available",
            }

        # 3️⃣ Build document context
        document_context = "\n\n".join(
            f"[PAGE {c.page_number}]\n{c.chunk_text}"
            for c in chunks
        )[:MAX_CONTEXT_CHARS]

        # 4️⃣ Decide prompt type
        if existing:
            # 🔁 REFINEMENT MODE
            system_prompt = BASE_SYSTEM_PROMPT + "\n" + REFINEMENT_INSTRUCTIONS

            user_prompt = f"""
DOCUMENT CONTENT:
{document_context}

PREVIOUS SUMMARY:
{existing.summery_text}

Improve the summary.
"""
        else:
            # 🆕 FIRST GENERATION
            system_prompt = BASE_SYSTEM_PROMPT

            user_prompt = f"""
DOCUMENT CONTENT:
{document_context}

Generate the summary.
"""

        # 5️⃣ SINGLE LLM CALL
        llm_res = ask_llm(
            context=system_prompt,
            question=user_prompt,
        )

        parsed = safe_json_parse(llm_res["data"]["answer"])

        # 6️⃣ SAVE (UPSERT)
        if existing:
            existing.summery_text = parsed["summary"]
            existing.tags = parsed["tags"]
            existing.citations = parsed["citations"]
            cached = True
        else:
            existing = DocuemntSummery(
                document_id=document_id,
                summery_text=parsed["summary"],
                tags=parsed["tags"],
                citations=parsed["citations"],
            )
            db.add(existing)
            cached = False

        db.commit()

        return {
            "status": "success",
            "document_id": document_id,
            "summary": parsed["summary"],
            "tags": parsed["tags"],
            "citations": parsed["citations"],
            "refined": cached,
        }

    finally:
        db.close()
