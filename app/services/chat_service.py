from typing import List, Tuple
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


# =========================
# CONFIG
# =========================
TOP_K = 5               # number of chunks to retrieve
MAX_CHAT_HISTORY = 6    # recent messages only


# =========================
# HELPERS
# =========================
def load_recent_chat_history(
    db: Session,
    session_id: str,
    limit: int = MAX_CHAT_HISTORY,
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


def semantic_search_chunks(
    db: Session,
    *,
    ai_document_id: int,
    query_embedding: List[float],
    top_k: int = TOP_K,
) -> List[DocumentChunk]:
    """
    Pure embedding-based semantic search.
    NO LLM calls here.
    SAFE for numpy / pgvector / list embeddings.
    """

    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.ai_document_id == ai_document_id)
        .all()
    )

    scored: List[Tuple[float, DocumentChunk]] = []

    for chunk in chunks:
        # ✅ SAFE embedding check (CRITICAL FIX)
        if chunk.embedding is None or len(chunk.embedding) == 0:
            continue

        # Dot-product similarity (nomic vectors are normalized)
        score = sum(
            qe * ce
            for qe, ce in zip(query_embedding, chunk.embedding)
        )

        scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [chunk for _, chunk in scored[:top_k]]


# =========================
# MAIN CHAT API
# =========================
def chatWithDocument(
    *,
    document_id: int,
    session_id: str,
    query: str,
) -> dict:
    """
    🔥 EMBEDDING-BASED DOCUMENT CHAT
    - ONE embedding call
    - ONE LLM call
    """

    with SessionLocal() as db:

        # ---------------------------------
        # 1️⃣ Resolve AI document
        # ---------------------------------
        ai_doc = (
            db.query(AIDocument)
            .filter(AIDocument.document_id == document_id)
            .first()
        )

        if not ai_doc:
            return {
                "status": "processing",
                "message": "Document ingestion not completed yet",
            }

        # ---------------------------------
        # 2️⃣ Store user message
        # ---------------------------------
        db.add(
            SessionMessage(
                session_id=session_id,
                document_id=ai_doc.id,
                role="user",
                content=query,
            )
        )
        db.commit()

        # ---------------------------------
        # 3️⃣ Load recent chat memory
        # ---------------------------------
        chat_history = load_recent_chat_history(db, session_id)

        # ---------------------------------
        # 4️⃣ Create query embedding (🔥 ONE CALL 🔥)
        # ---------------------------------
        query_embedding = createEmbedding(query)

        # ---------------------------------
        # 5️⃣ Semantic retrieval
        # ---------------------------------
        top_chunks = semantic_search_chunks(
            db,
            ai_document_id=ai_doc.id,
            query_embedding=query_embedding,
            top_k=TOP_K,
        )

        context_parts = []
        citations = []

        for c in top_chunks:
            context_parts.append(f"[PAGE {c.page_number}] {c.chunk_text}")
            citations.append({"page_number": c.page_number})

        # ---------------------------------
        # 6️⃣ Fallback to summary if needed
        # ---------------------------------
        if not context_parts:
            summary = (
                db.query(DocumentSummary)
                .filter(DocumentSummary.ai_document_id == ai_doc.id)
                .first()
            )

            if summary and summary.summary_text:
                context_parts.append(summary.summary_text)
                citations = summary.citations or []
            else:
                context_parts.append(
                    "The provided document does not contain relevant information."
                )

        # ---------------------------------
        # 7️⃣ Build LLM context
        # ---------------------------------
        final_context_parts = []

        if chat_history:
            final_context_parts.append(
                f"CHAT HISTORY:\n{chat_history}"
            )

        final_context_parts.append(
            "DOCUMENT CONTEXT:\n" + "\n\n".join(context_parts)
        )

        final_context = "\n\n".join(final_context_parts)

        # ---------------------------------
        # 8️⃣ 🔥 SINGLE LLM CALL 🔥
        # ---------------------------------
        llm_result = askLlm(
            context=final_context,
            question=query,
        )

        answer = llm_result["data"]["answer"]

        # ---------------------------------
        # 9️⃣ Store assistant reply
        # ---------------------------------
        db.add(
            SessionMessage(
                session_id=session_id,
                document_id=ai_doc.id,
                role="assistant",
                content=answer,
            )
        )
        db.commit()

        return {
            "status": "success",
            "answer": answer,
            "citations": citations,
        }