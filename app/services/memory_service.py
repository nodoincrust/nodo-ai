from sqlalchemy.orm import Session
from app.models import SessionMessages, SessionMemorySummery
from app.AIhelpers.llm_helper import ask_llm


def update_memory_summary(db: Session, session_id: str):
    messages = db.query(SessionMessages)\
                 .filter_by(session_id=session_id)\
                 .order_by(SessionMessages.created_at)\
                 .all()

    if not messages:
        return

    convo = "\n".join(f"{m.role}: {m.content}" for m in messages)

    resp = ask_llm(
        context="Summarize conversation memory.",
        question=convo
    )
    if resp.get("status") != "success":
        # skip updating memory if LLM unavailable
        return
    summary = resp["data"]["answer"]

    existing = db.query(SessionMemorySummery)\
                 .filter_by(session_id=session_id)\
                 .first()

    if existing:
        existing.summary = summary
    else:
        db.add(SessionMemorySummery(
            session_id=session_id,
            summary=summary
        ))