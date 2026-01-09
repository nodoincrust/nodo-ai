from typing import Dict
from sqlalchemy.orm import Session

from app.models import (
    AIDocument,
    SessionMessage,
    SessionMemorySummary,
)
from app.AIhelpers.llm_helper import askLlm
from app.services.background_tasks import submitMemoryUpdate


# =========================
# AI DOCUMENT RESOLUTION
# =========================

def getAiDocument(db: Session, document_id: int):
    return (
        db.query(AIDocument)
        .filter(AIDocument.document_id == document_id)
        .first()
    )  # Fetches AI document for given document_id


# =========================
# CHAT MESSAGE PERSISTENCE
# =========================

def saveMessage(
    db: Session,
    *,
    session_id: str,
    ai_document_id: int,
    role: str,
    content: str,
):
    db.add(
        SessionMessage(
            session_id=session_id,
            ai_document_id=ai_document_id,
            role=role,
            content=content,
        )
    )  # Saves a single chat message


# =========================
# SESSION MEMORY FETCH
# =========================

def getSessionMemorySummary(db: Session, session_id: str) -> str:
    record = (
        db.query(SessionMemorySummary)
        .filter(SessionMemorySummary.session_id == session_id)
        .first()
    )
    return record.summary if record else ""  # Returns cached memory summary


# =========================
# MEMORY-AWARE LLM CALL
# =========================

def askLlmWithMemory(
    *,
    db: Session,
    session_id: str,
    context: str,
    question: str,
) -> Dict[str, Dict[str, str]]:

    memory = getSessionMemorySummary(db, session_id)  # Loads long-term memory if available

    finalContext = (
        f"MEMORY:\n{memory}\n\n{context}"
        if memory else context
    )  # Injects memory only when present

    result = askLlm(
        context=finalContext,
        question=question,
    )  # Executes optimized LLM call

    submitMemoryUpdate(session_id)  # Triggers background memory summarization

    return result