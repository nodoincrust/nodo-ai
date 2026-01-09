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

# INTENT DETECTION
DOCUMENT_HINTS = (
    "this document","in this document","according to","mentioned","described","page","section","key issue",)

GENERAL_qPATTERN = (
    "what is ","what does ","define ","explain ","meaning of ","what do you mean by ",)


def is_general_question(query: str) -> bool:
    q = query.lower().strip()       #Normalizes input for reliable keyword matching

    if any(k in q for k in DOCUMENT_HINTS):
        return False

    if q.startswith(GENERAL_qPATTERN):
        return True

    return len(q.split()) <= 4

# VECTORSPACE SIMILARITY
def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))              #Measures directional similarity in vectors
    mag_a = sqrt(sum(x * x for x in a))         #vector magnitudes for normalization
    mag_b = sqrt(sum(y * y for y in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


# Docuemnt context retrieval
def retrieve_document_context(
    db: Session,
    ai_document_id: int,
    query: str,
    top_k: int = 4,
) -> Tuple[str, list]:

    query_embedding = createEmbedding(query)     #user query into a vector

    # Loads all stored chunks
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == ai_document_id)
        .all()
    )

    #semantic similarity to the query
    scored = [
        (cosine_similarity(query_embedding, c.embedding), c)
        for c in chunks
        if c.embedding is not None
    ]

    if not scored:
        return "", []

    scored.sort(key=lambda x: x[0], reverse=True)       #Ranks chunks by similarity with query 
    top_chunks = scored[:top_k]

    context = "\n\n".join(
        f"[PAGE {c.page_number}] {c.chunk_text}"
        for _, c in top_chunks
    )

    citations = [{"page_number": c.page_number} for _, c in top_chunks]

    return context, citations

#loding the previous chat history
def load_recent_chat_history(
    db: Session,
    session_id: str,
    limit: int = 6,
) -> str:
    messages = (
        db.query(SessionMessage)
        .filter(SessionMessage.session_id == session_id)
        .order_by(SessionMessage.created_at.desc())
        .limit(limit)
        .all()
    )

    messages.reverse()

    return "\n".join(
        f"{m.role.upper()}: {m.content}"
        for m in messages
    )

#chat with document function
def chatWithDocument(
    *,
    document_id: int,
    session_id: str,
    query: str,
) -> dict:

    with SessionLocal() as db:

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
        
        # Extracts internal AI document ID
        ai_document_id = ai_doc.id

        chat_history = load_recent_chat_history(db, session_id)  # Loads recent chat messages for continuity

        context = ""
        citations = []
        general_question = False

        context, citations = retrieve_document_context(
            db, ai_document_id, query
        )

        if not context:
            summary = (
                db.query(DocumentSummary)
                .filter(DocumentSummary.document_id == ai_document_id)
                .first()
            ) 
        # Falls back to document summary if chunks are missing
            if summary and summary.summary_text:
                context = summary.summary_text
                citations = summary.citations or []
            else:
                general_question = True 

        system_prompt = (
            GENERAL_SYSTEM_PROMPT if general_question
            else DOCUMENT_SYSTEM_PROMPT
        )

        full_context_parts = [system_prompt]

        if chat_history:
            full_context_parts.append(f"CHAT HISTORY:\n{chat_history}")  # Injects previous conversation

        if context:
            full_context_parts.append(f"DOCUMENT:\n{context}")

        full_context = "\n\n".join(full_context_parts) 

        llm_result = askLlm(
            context=full_context,
            question=query,
        )

        answer = llm_result["data"]["answer"]

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
            "answer": answer,
            "citations": [] if general_question else citations,
        }

# Previous Chat Display
def fetchAllMessages(db: Session, *, sessionId: str) -> list:
    return (
        db.query(SessionMessage)
        .filter(SessionMessage.session_id == sessionId)
        .order_by(SessionMessage.created_at.asc())
        .all()
    )