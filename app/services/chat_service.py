from typing import Optional, Generator, List
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import SessionMessages, SessionMemorySummery, DocuementChunks
from app.AIhelpers.embedding_helper import create_embedding
from app.AIhelpers.llm_helper import ask_llm, ask_llm_stream

# ==============================
# CONFIG
# ==============================

TOP_K = 5
MAX_HISTORY = 6


def _load_context(db: Session, session_id: str) -> str:

    memory = (
        db.query(SessionMemorySummery)
        .filter_by(session_id=session_id)
        .first()
    )

    messages = (
        db.query(SessionMessages)
        .filter_by(session_id=session_id)
        .order_by(SessionMessages.created_at.desc())
        .limit(MAX_HISTORY)
        .all()
    )

    history = "\n".join(
        f"{m.role.upper()}: {m.content}"
        for m in reversed(messages)
    )

    if memory and memory.summary:
        return f"MEMORY:\n{memory.summary}\n\n{history}"

    return history


def _retrieve_document_context(
    db: Session,
    *,
    query: str,
    document_id: Optional[str],
) -> tuple[str, list]:
    """
    Semantic retrieval ONLY.
    No LLM calls.
    """
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
        citations.append(
            {
                "document_id": str(c.document_id),
                "chunk_index": c.chunk_index,
                "page_number": c.page_number,
            }
        )

    return "\n".join(context), citations


# ==============================
# PUBLIC CHAT API
# ==============================

def chat_with_session(
    session_id: str,
    query: str,
    document_id: Optional[str] = None,
) -> dict:
    """
    🔥 EXACTLY ONE LLM CALL HAPPENS HERE 🔥
    """
    db = SessionLocal()
    try:
        # 1️⃣ Save user message
        db.add(
            SessionMessages(
                session_id=session_id,
                role="user",
                content=query,
            )
        )
        db.commit()

        # 2️⃣ Build context (NO LLM)
        memory_context = _load_context(db, session_id)
        doc_context, citations = _retrieve_document_context(
            db,
            query=query,
            document_id=document_id,
        )

        full_context = memory_context
        if doc_context:
            full_context += "\n\nDOCUMENT:\n" + doc_context

        # 3️⃣ 🔥 SINGLE LLM CALL 🔥
        llm_result = ask_llm(
            context=full_context,
            question=query,
        )

        answer = llm_result["data"]["answer"]

        # 4️⃣ Save assistant response
        db.add(
            SessionMessages(
                session_id=session_id,
                role="assistant",
                content=answer,
            )
        )
        db.commit()

        return {
            "answer": answer,
            "citations": citations,
        }

    finally:
        db.close()


def chat_stream(
    session_id: str,
    query: str,
) -> Generator[str, None, None]:
    """
    Streaming version.
    Still ONE logical LLM call.
    """
    db = SessionLocal()
    try:
        db.add(
            SessionMessages(
                session_id=session_id,
                role="user",
                content=query,
            )
        )
        db.commit()

        context = _load_context(db, session_id)
        final_tokens: List[str] = []

        for token in ask_llm_stream(context=context, question=query):
            final_tokens.append(token)
            yield token

        db.add(
            SessionMessages(
                session_id=session_id,
                role="assistant",
                content="".join(final_tokens),
            )
        )
        db.commit()

    finally:
        db.close()