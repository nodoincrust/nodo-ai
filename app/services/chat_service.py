import logging
from typing import List, Tuple
from math import sqrt
import math
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    AIDocument,
    DocumentChunk,
    DocumentSummary,
    SessionMessage,
)
from app.AIhelpers.embedding_helper import createEmbedding
from app.AIhelpers.llm_helper import askLlm
logger = logging.getLogger("ai.chatService")

# =====================================================
# SYSTEM PROMPTS
# =====================================================

DOCUMENT_SYSTEM_PROMPT = """
You are an AI assistant answering questions using the PROVIDED DOCUMENT CONTENT BELOW.

IMPORTANT:
- The document content IS PROVIDED below.
- You MUST answer strictly from it.
- NEVER say the document is missing or not provided.
- If the answer is not found, say:
  "The provided document does not contain this information."

Rules:
- Prefer document facts
- Do NOT hallucinate
- Be concise and factual
"""

GENERAL_SYSTEM_PROMPT = """You are a knowledgeable AI assistant.
Rules:
- Answer using general knowledge
- Be clear and concise
- Do NOT hallucinate
"""

# =====================================================
# INTENT DETECTION (FAST, NO LLM)
# =====================================================

DOCUMENT_HINTS = (
    "this document","in this document","according to","mentioned","described","page","section","key issue",)

GENERAL_qPATTERN = (
    "what is ","what does ","define ","explain ","meaning of ","what do you mean by ",)


def is_general_question(query: str) -> bool:
    q = query.lower().strip()

    if any(k in q for k in DOCUMENT_HINTS):
        return False

    if q.startswith(GENERAL_qPATTERN):
        return True

    return len(q.split()) <= 4

# VECTORS

def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sqrt(sum(x * x for x in a))
    mag_b = sqrt(sum(y * y for y in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


# =====================================================
# DOCUMENT CONTEXT RETRIEVAL
# =====================================================

def retrieve_document_context(
    db: Session,
    ai_document_id: int,
    query: str,
    top_k: int = 4,
) -> Tuple[str, list]:

    query_embedding = createEmbedding(query)

    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == ai_document_id)
        .all()
    )

    scored = [
        (cosine_similarity(query_embedding, c.embedding), c)
        for c in chunks
        if c.embedding is not None
    ]

    if not scored:
        return "", []

    scored.sort(key=lambda x: x[0], reverse=True)
    top_chunks = scored[:top_k]

    context = "\n\n".join(
        f"[PAGE {c.page_number}] {c.chunk_text}"
        for _, c in top_chunks
    )

    citations = [{"page_number": c.page_number} for _, c in top_chunks]

    return context, citations


# =====================================================
# MAIN CHAT FUNCTION
# =====================================================

def chatWithDocument(
    *,
    document_id: int,
    session_id: str,
    query: str,
) -> dict:

    with SessionLocal() as db:

        # 1️⃣ Resolve AI document (single query)
        ai_doc = (
            db.query(AIDocument.id)
            .filter(AIDocument.document_id == document_id)
            .first()
        )

        if not ai_doc:
            return {
                "status": "processing",
                "message": "Document ingestion not completed yet",
            }

        ai_document_id = ai_doc.id

        # 2️⃣ Intent detection
        general_question = is_general_question(query)

        context = ""
        citations = []

        # 3️⃣ Retrieve document context only if needed
        if not general_question:
            context, citations = retrieve_document_context(
                db, ai_document_id, query
            )

            if not context:
                summary = (
                    db.query(DocumentSummary)
                    .filter(DocumentSummary.document_id == ai_document_id)
                    .first()
                )
                if summary:
                    context = summary.summary_text or ""
                    citations = summary.citations or []

        # 4️⃣ Prompt assembly (minimal tokens)
        system_prompt = (
            GENERAL_SYSTEM_PROMPT if general_question
            else DOCUMENT_SYSTEM_PROMPT
        )

        llm_prompt = f"{system_prompt}\n\n{context}\n\nQuestion:\n{query}"

        # 5️⃣ LLM call (single call)
        llm_result = askLlm(context=llm_prompt, question=query)
        answer = llm_result["data"]["answer"]

        # 6️⃣ Persist chat
        db.add_all([
            SessionMessage(
                session_id=session_id,
                document_id=ai_document_id,
                role="user",
                content=query,
            ),
            SessionMessage(
                session_id=session_id,
                document_id=ai_document_id,
                role="assistant",
                content=answer,
            ),
        ])
        db.commit()

        return {
            "status": "success",
            # "document_id": document_id,
            # "session_id": session_id,
            "answer": answer,
            "citations": [] if general_question else citations,
        }