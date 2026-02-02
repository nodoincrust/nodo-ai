from sqlalchemy.orm import Session
from app.models import SessionMessage, SessionMemorySummary
from app.AIhelpers.llm_helper import askLlm

MEMORY_SUMMARY_THRESHOLD = 10  # summarize after N messages
KEEP_LAST_MESSAGES = 10       # messages to keep after summarization

MEMORY_SYSTEM_PROMPT = """
You are an AI assistant that summarizes conversation history.

Rules:
- Produce a concise factual summary
- Focus on user intent, decisions, and context
- Do NOT output JSON
- Do NOT invent information
"""

def pruneOldMessages(
    db: Session,
    *,
    sessionId: str,
    keep_last: int = KEEP_LAST_MESSAGES,
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
) -> bool:
    messages = (
        db.query(SessionMessage)
        .filter_by(session_id=sessionId)
        .order_by(SessionMessage.created_at.asc())
        .all()
    )

    if not messages:
        return False

    message_count = len(messages)

    if message_count < MEMORY_SUMMARY_THRESHOLD:
        return False
    conversationText = "\n".join(
        f"{m.role.upper()}: {m.content}" for m in messages
    )

    prompt = (
        "Summarize the conversation below.\n"
        "Keep it concise and factual.\n\n"
        f"{conversationText}"
    )

    llmResult = askLlm(
        context=prompt,
        question="Summarize conversation.",
        system_prompt=MEMORY_SYSTEM_PROMPT,
    )

    if llmResult.get("status") != "success":
        return False

    summaryText = llmResult["data"]["answer"]

    existing = (
        db.query(SessionMemorySummary)
        .filter_by(session_id=sessionId)
        .first()
    )

    if existing:
        existing.summary = summaryText
    else:
        db.add(
            SessionMemorySummary(
                session_id=sessionId,
                summary=summaryText,
            )
        )
    pruneOldMessages(
        db,
        sessionId=sessionId,
        keep_last=KEEP_LAST_MESSAGES,
    )

    db.commit()
    return True
