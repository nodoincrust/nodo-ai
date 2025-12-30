# ⭐ CORE: Memory + RAG + Citations
from typing import Generator, Optional
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    SessionMessages,
    SessionMemorySummery,
    DocuementChunks,
)

from app.AIhelpers.embedding_helper import create_embedding
from app.AIhelpers.llm_helper import ask_llm, ask_llm_stream
from app.services.background_tasks import maybe_update_memory

TOP_K = 5
MAX_HISTORY = 6


def _load_context(db: Session, session_id: str):
    memory = db.query(SessionMemorySummery).filter_by(session_id=session_id).first()

    messages = (
        db.query(SessionMessages)
        .filter_by(session_id=session_id)
        .order_by(SessionMessages.created_at.desc())
        .limit(MAX_HISTORY)
        .all()
    )

    return (memory.summary if memory else ""), list(reversed(messages))


def _retrieve_chunks(db: Session, query: str):
    query_emb = create_embedding(query)

    chunks = db.query(DocuementChunks).all()

    scored = []
    for c in chunks:
        if c.embedding:
            score = sum(a * b for a, b in zip(query_emb, c.embedding))
            scored.append((score, c))

    scored.sort(reverse=True)

    context, citations = [], []
    for _, c in scored[:TOP_K]:
        context.append(c.chunk_text)
        citations.append(
            {
                "document_id": str(c.document_id),
                "chunk_index": c.chunk_index,
            }
        )

    return "\n".join(context), citations


def chat_with_session(session_id: str, query: str) -> dict:
    repo = DBRepo()
    try:
        if not repo.session_exists(session_id):
            raise ValueError("Session not found")

        repo.add_message(session_id, "user", query)

        history = repo.get_recent_messages(session_id, MAX_HISTORY)
        context = "\n".join(f"{r.upper()}: {c}" for r, c in history)

        answer = ask_llm(context=context, question=query)

        if answer["status"] == "success":
            repo.add_message(session_id, "assistant", answer["data"]["answer"])
            repo.db.commit()
            maybe_update_memory(repo, session_id)

        return answer
    finally:
        repo.close()


def chat_stream(session_id: str, query: str) -> Generator[str, None, None]:
    repo = DBRepo()
    try:
        if not repo.session_exists(session_id):
            raise ValueError("Session not found")

        repo.add_message(session_id, "user", query)

        history = repo.get_recent_messages(session_id, MAX_HISTORY)
        context = "\n".join(f"{r.upper()}: {c}" for r, c in history)

        final = []
        for token in ask_llm_stream(context=context, question=query):
            final.append(token)
            yield token

        repo.add_message(session_id, "assistant", "".join(final))
        repo.db.commit()
        maybe_update_memory(repo, session_id)
    finally:
        repo.close()


def chat_with_citation(
    session_id: str,
    query: str,
    document_id: Optional[str] = None,
) -> dict:
    repo = DBRepo()
    try:
        if not repo.session_exists(session_id):
            raise ValueError("Session not found")

        repo.add_message(session_id, "user", query)

        history = repo.get_recent_messages(session_id, MAX_HISTORY)
        context_lines = [f"{r.upper()}: {c}" for r, c in history]

        citations = []
        if document_id:
            q_emb = create_embedding(query)
            chunks = repo.semantic_search(
                document_id=document_id,
                query_embedding=q_emb,
                limit=TOP_K,
                session_id=session_id,
            )
            for c in chunks:
                context_lines.append(c.chunk_text)
                citations.append(
                    {
                        "document_id": str(c.document_id),
                        "chunk_index": c.chunk_index,
                    }
                )

        context = "\n".join(context_lines)
        answer = ask_llm(context=context, question=query)

        if answer["status"] == "success":
            answer["data"]["citations"] = citations
            repo.add_message(session_id, "assistant", answer["data"]["answer"])
            repo.db.commit()
            maybe_update_memory(repo, session_id)

        return answer
    finally:
        repo.close()
