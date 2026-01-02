"""
BACKGROUND TASKS
- NO direct LLM calls here
- Lightweight, safe, debounced
"""

import logging
import asyncio
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from app.db import SessionLocal
from app.models import SessionMessages, SessionMemorySummery
from app.services.memory_service import update_memory_summary

logger = logging.getLogger("ai_modul.background_tasks")

# ==============================
# CONFIG
# ==============================

MEMORY_UPDATE_INTERVAL = 10   # messages
MAX_WORKERS = 4               # THREADS, not processes

_executor: Optional[ThreadPoolExecutor] = None


def get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    return _executor

def submit_memory_update(session_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _run_memory_update(session_id)
        return

    loop.run_in_executor(
        get_executor(),
        _run_memory_update,
        session_id,
    )

def _run_memory_update(session_id: str) -> None:
    db = SessionLocal()
    try:
        count = (
            db.query(SessionMessages)
            .filter_by(session_id=session_id)
            .count()
        )

        # Only run at exact intervals
        if count == 0 or count % MEMORY_UPDATE_INTERVAL != 0:
            return

        # Debounce: already summarized?
        existing = (
            db.query(SessionMemorySummery)
            .filter_by(session_id=session_id, message_count=count)
            .first()
        )
        if existing:
            return

        logger.info(
            "Updating memory summary for session=%s messages=%s",
            session_id,
            count,
        )

        update_memory_summary(db, session_id, message_count=count)
        db.commit()

    except Exception:
        logger.exception(
            "Background memory update failed for session %s",
            session_id,
        )
        db.rollback()
    finally:
        db.close()

def shutdown_executor() -> None:
    global _executor
    if _executor:
        logger.info("Shutting down background executor")
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
