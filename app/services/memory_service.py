from sqlalchemy.orm import Session
from app.models import SessionMessage, SessionMemorySummary
from app.AIhelpers.llm_helper import askLlm


def updateMemorySummary(
    db: Session,
    *,
    sessionId: str,
    messageCount: int | None = None,
) -> None:
    messages = (
        db.query(SessionMessage)
        .filter_by(session_id=sessionId)
        .order_by(SessionMessage.created_at.asc())
        .all()
    )

    if not messages:
        return

    conversationText = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)

    prompt = (
        "Summarize the conversation below.\n"
        "Do NOT include document details unless explicitly discussed.\n"
        "Keep it concise and factual.\n\n"
        f"{conversationText}"
    )

    llmResult = askLlm(context=prompt, question="Summarize conversation.")
    summaryText = llmResult["data"]["answer"]

    existing = db.query(SessionMemorySummary).filter_by(session_id=sessionId).first()

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