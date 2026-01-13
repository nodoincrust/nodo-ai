from typing import List
from sqlalchemy.orm import Session
# from sqlalchemy import func

from app.db import SessionLocal
from app.models import (
    AIDocument,
    DocumentChunk,
    DocumentSummary,
    SessionMessage,
)
from app.AIhelpers.embedding_helper import createEmbedding
from app.AIhelpers.llm_helper import askLlm

TOP_K = 15               # number of chunks to retrieve
MAX_CHAT_HISTORY = 6    # recent messages only

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
    return (
        db.query(DocumentChunk)
        .filter(DocumentChunk.ai_document_id == ai_document_id)
        .order_by(
            DocumentChunk.embedding.cosine_distance(query_embedding)
        )
        .limit(top_k)
        .all()
    )

def chatWithDocument(
    *,
    document_id: int,
    session_id: str,
    query: str,
) -> dict:

    with SessionLocal() as db:

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

        db.add(
            SessionMessage(
                session_id=session_id,
                document_id=ai_doc.id,
                role="user",
                content=query,
            )
        )
        db.commit()

        chat_history = load_recent_chat_history(db, session_id)

        query_embedding = createEmbedding(query.strip().lower())

        #Sementic search accross the chunks
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

        final_context_parts = []

        if chat_history:
            final_context_parts.append(
                f"CHAT HISTORY:\n{chat_history}"
            )

        final_context_parts.append(
            "DOCUMENT CONTEXT:\n" + "\n\n".join(context_parts)
        )

        final_context = "\n\n".join(final_context_parts)

        llm_result = askLlm(
            context=final_context,
            question=query,
        )

        answer = llm_result["data"]["answer"]

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