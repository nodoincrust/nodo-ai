from typing import Generator, List, Tuple
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    SessionMessage,
    SessionMemorySummary,   # ✅ CORRECT MODEL
    DocumentChunk,
)
from app.AIhelpers.embedding_helper import createEmbedding
from app.AIhelpers.llm_helper import askLlm, askLlmStream

# ==============================
# CONFIG
# ==============================

TOP_K = 5
MAX_HISTORY = 6


# ==============================
# INTERNAL HELPERS
# ==============================

def loadContext(db: Session, sessionId: str) -> str:
    """
    Load recent chat history + compressed memory.
    NO LLM calls here.
    """
    memory = (
        db.query(SessionMemorySummary)
        .filter_by(session_id=sessionId)
        .first()
    )

    messages = (
        db.query(SessionMessage)
        .filter_by(session_id=sessionId)
        .order_by(SessionMessage.created_at.desc())
        .limit(MAX_HISTORY)
        .all()
    )

    historyText = "\n".join(
        f"{m.role.upper()}: {m.content}"
        for m in reversed(messages)
    )

    if memory and memory.summary:
        return f"MEMORY:\n{memory.summary}\n\n{historyText}"

    return historyText


def retrieveDocumentContext(
    db: Session,
    *,
    documentId: int,
    query: str,
) -> Tuple[str, list]:
    """
    Semantic retrieval ONLY.
    NO LLM calls.
    """
    queryEmbedding = createEmbedding(query)

    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == documentId)
        .all()
    )

    scored: List[Tuple[float, DocumentChunk]] = []
    for chunk in chunks:
        if chunk.embedding:
            score = sum(a * b for a, b in zip(queryEmbedding, chunk.embedding))
            scored.append((score, chunk))

    scored.sort(reverse=True)

    contextParts: List[str] = []
    citations: List[dict] = []

    for _, chunk in scored[:TOP_K]:
        contextParts.append(chunk.chunk_text)
        citations.append(
            {
                "documentId": chunk.document_id,
                "chunkIndex": chunk.chunk_index,
                "pageNumber": chunk.page_number,
            }
        )

    return "\n".join(contextParts), citations


# ==============================
# PUBLIC CHAT API
# ==============================

def chatWithDocument(
    *,
    documentId: int,
    sessionId: str,
    query: str,
) -> dict:
    """
    🔥 EXACTLY ONE LLM CALL HAPPENS HERE 🔥
    """
    db = SessionLocal()
    try:
        # 1️⃣ Save user message
        db.add(
            SessionMessage(
                session_id=sessionId,
                role="user",
                content=query,
            )
        )
        db.commit()

        # 2️⃣ Build context (NO LLM)
        memoryContext = loadContext(db, sessionId)
        documentContext, citations = retrieveDocumentContext(
            db,
            documentId=documentId,
            query=query,
        )

        fullContext = memoryContext
        if documentContext:
            fullContext += "\n\nDOCUMENT:\n" + documentContext

        # 3️⃣ 🔥 SINGLE LLM CALL 🔥
        llmResult = askLlm(
            context=fullContext,
            question=query,
        )

        answer = llmResult["data"]["answer"]

        # 4️⃣ Save assistant response
        db.add(
            SessionMessage(
                session_id=sessionId,
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


def chatStream(
    *,
    sessionId: str,
    query: str,
) -> Generator[str, None, None]:
    """
    Streaming version.
    Still ONE logical LLM call.
    """
    db = SessionLocal()
    try:
        db.add(
            SessionMessage(
                session_id=sessionId,
                role="user",
                content=query,
            )
        )
        db.commit()

        context = loadContext(db, sessionId)
        finalTokens: List[str] = []

        for token in askLlmStream(context=context, question=query):
            finalTokens.append(token)
            yield token

        db.add(
            SessionMessage(
                session_id=sessionId,
                role="assistant",
                content="".join(finalTokens),
            )
        )
        db.commit()

    finally:
        db.close()