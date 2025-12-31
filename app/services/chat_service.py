from typing import Generator, Optional, List
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import SessionMessages, SessionMemorySummery, DocuementChunks
from app.AIhelpers.embedding_helper import create_embedding
from app.AIhelpers.llm_helper import ask_llm, ask_llm_stream
# from services.background_tasks import maybe_update_memory

TOP_K = 5
MAX_HISTORY = 6


def _load_context(db: Session, session_id: str) -> str:
    memory = db.query(SessionMemorySummery)\
               .filter_by(session_id=session_id)\
               .first()

    messages = db.query(SessionMessages)\
                 .filter_by(session_id=session_id)\
                 .order_by(SessionMessages.created_at.desc())\
                 .limit(MAX_HISTORY)\
                 .all()

    history = "\n".join(
        f"{m.role.upper()}: {m.content}"
        for m in reversed(messages)
    )

    return f"MEMORY:\n{memory.summary}\n\n{history}" if memory else history


def _retrieve_chunks(db: Session, query: str, document_id: Optional[str]):
    if not document_id:
        return "", []

    query_emb = create_embedding(query)

    chunks = (
        db.query(DocuementChunks)
        .filter(DocuementChunks.document_id == document_id)
        .all()
    )

    scored = []
    for c in chunks:
        if c.embedding:
            score = sum(a * b for a, b in zip(query_emb, c.embedding))
            scored.append((score, c))

    scored.sort(reverse=True)

    context = []
    citations = []

    for _, c in scored[:TOP_K]:
        context.append(c.chunk_text)
        citations.append({
            "document_id": str(c.document_id),
            "chunk_index": c.chunk_index,
            "page_number": c.page_number
        })
    return "\n".join(context), citations


def chat(
    session_id: str,
    query: str,
    document_id: Optional[str] = None
) -> dict:
    db = SessionLocal()
    try:
        db.add(SessionMessages(
            session_id=session_id,
            role="user",
            content=query
        ))
        db.commit()

        base_context = _load_context(db, session_id)
        doc_context, citations = _retrieve_chunks(db, query, document_id)

        full_context = f"{base_context}\n\nDOCUMENT:\n{doc_context}"

        result = ask_llm(context=full_context, question=query)

        answer = result["data"]["answer"]

        db.add(SessionMessages(
            session_id=session_id,
            role="assistant",
            content=answer
        ))
        db.commit()

        # maybe_update_memory(session_id)

        return {
            "answer": answer,
            "citations": citations
        }

    finally:
        db.close()


def chat_stream(
    session_id: str,
    query: str
) -> Generator[str, None, None]:
    db = SessionLocal()
    try:
        db.add(SessionMessages(
            session_id=session_id,
            role="user",
            content=query
        ))
        db.commit()

        context = _load_context(db, session_id)
        final = []

        for token in ask_llm_stream(context=context, question=query):
            final.append(token)
            yield token

        db.add(SessionMessages(
            session_id=session_id,
            role="assistant",
            content="".join(final)
        ))
        db.commit()

        # maybe_update_memory(session_id)

    finally:
        db.close()