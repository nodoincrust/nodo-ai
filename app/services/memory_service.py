from sqlalchemy.orm import Session

from app.models import SessionMessages, SessionMemorySummery
from app.AIhelpers.llm_helper import askLlm


def updateMemorySummary(
    db: Session,
    *,
    sessionId: str,
    messageCount: int | None = None,
) -> None:
    messages = (
        db.query(SessionMessages)
        .filter_by(session_id=sessionId)
        .order_by(SessionMessages.created_at.asc())
        .all()
    )

    if not messages:
        return

    conversationText = "\n".join(
        f"{m.role.upper()}: {m.content}" for m in messages
    )

    llmResult = askLlm(
        context="Summarize conversation memory.",
        question=conversationText,
    )

    summaryText = llmResult["data"]["answer"]

    existing = (
        db.query(SessionMemorySummery)
        .filter_by(session_id=sessionId)
        .first()
    )

    if existing:
        existing.summary = summaryText
        if messageCount is not None:
            existing.message_count = messageCount
    else:
        db.add(
            SessionMemorySummery(
                session_id=sessionId,
                summary=summaryText,
                message_count=messageCount,
            )
        )
