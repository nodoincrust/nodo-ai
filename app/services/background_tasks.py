from app.db import SessionLocal
from app.models import SessionMessages
from app.services.memory_service import update_memory_summary

TRIGGER_COUNT = 10


def maybe_update_memory(session_id: str):
    db = SessionLocal()
    try:
        count = db.query(SessionMessages)\
                  .filter_by(session_id=session_id)\
                  .count()

        if count % TRIGGER_COUNT == 0:
            update_memory_summary(db, session_id)
            db.commit()
    finally:
        db.close()
