from sqlalchemy.orm import Session
from app.models import SessionMessage, SessionMemorySummary
from app.AIhelpers.llm_helper import askLlm

def pruneOldMessages(
    db: Session,
    *,
    sessionId: str,
    keep_last: int = 50,
) -> int:
    """
    Delete old chat messages after memory summary is updated.
    Keeps only the latest `keep_last` messages.
    """

    subquery = (
        db.query(SessionMessage.id)
        .filter(SessionMessage.session_id == sessionId)
        .order_by(SessionMessage.created_at.desc())
        .limit(keep_last)
        .subquery()
    )

    deleted = (
        db.query(SessionMessage)
        .filter(SessionMessage.session_id == sessionId)
        .filter(SessionMessage.id.notin_(subquery))
        .delete(synchronize_session=False)
    )

    return deleted

def updateMemorySummary(
    db: Session,
    *,
    sessionId: str,
    messageCount: int | None = None,
) -> bool:
    messages = (
        db.query(SessionMessage)
        .filter_by(session_id=sessionId)
        .order_by(SessionMessage.created_at.asc())
        .all()
    )

    if not messages:
        return False

    conversationText = "\n".join(
        f"{m.role.upper()}: {m.content}" for m in messages
    )

    prompt = (
        "Summarize the conversation below.\n"
        "Keep it concise and factual.\n\n"
        f"{conversationText}"
    )

    llmResult = askLlm(context=prompt, question="Summarize conversation.")
    summaryText = llmResult["data"]["answer"]

    existing = (
        db.query(SessionMemorySummary)
        .filter_by(session_id=sessionId)
        .first()
    )

    if existing:
        existing.summary = summaryText
        if messageCount is not None:
            existing.message_count = messageCount
    else:
        db.add(
            SessionMemorySummary(
                session_id=sessionId,
                summary=summaryText,
                message_count=messageCount,
            )
        )

    return True