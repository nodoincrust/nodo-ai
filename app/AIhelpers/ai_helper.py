from typing import Dict
from sqlalchemy.orm import Session

from app.models import (
    AIDocument,
    SessionMessage,
    SessionMemorySummary,
)
from app.AIhelpers.llm_helper import askLlm
from app.services.background_tasks import submitMemoryUpdate


def getAiDocument(db: Session, document_id: int):
    return (
        db.query(AIDocument)
        .filter(AIDocument.document_id == document_id)      # Fetches AI document for given document_id
        .first()
    )  


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
    )


def getSessionMemorySummary(db: Session, session_id: str) -> str:
    record = (
        db.query(SessionMemorySummary)
        .filter(SessionMemorySummary.session_id == session_id)
        .first()
    )
    return record.summary if record else ""  # Returns cached memory summary


def askLlmWithMemory(
    *,
    db: Session,
    session_id: str,
    context: str,
    question: str,
    system_prompt: str,
) -> Dict[str, Dict[str, str]]:

    memory = getSessionMemorySummary(db, session_id)  # Loads long-term memory if available

    finalContext = (
        f"MEMORY:\n{memory}\n\n{context}"
        if memory else context
    )

    result = askLlm(
        context=finalContext,
        question=question,
        system_prompt=system_prompt,
    )

    submitMemoryUpdate(session_id)  # Triggers background memory summarization

    return result