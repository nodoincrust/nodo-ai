"""BACKGROUND TASK EXECUTION FOR AI + IO HEAVY WORK"""

import os
import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

from app.db import SessionLocal
from app.models import SessionMessages
from app.services.memory_service import update_memory_summary

logger = logging.getLogger("ai_modul.background_tasks")

# GLOBAL PROCESS POOL
MAX_WORKERS = max(1, os.cpu_count() // 2)

_AI_EXECUTOR: Optional[ProcessPoolExecutor] = None


def get_ai_executor() -> ProcessPoolExecutor:
    """ProcessPoolExecutor."""
    global _AI_EXECUTOR
    if _AI_EXECUTOR is None:
        logger.info("Creating AI ProcessPoolExecutor with %s workers", MAX_WORKERS)
        _AI_EXECUTOR = ProcessPoolExecutor(max_workers=MAX_WORKERS)
    return _AI_EXECUTOR

def submit_memory_update(session_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Not in event loop (rare, but safe fallback)
        _run_memory_update(session_id)
        return

    executor = get_ai_executor()

    loop.run_in_executor(
        executor,
        _run_memory_update,
        session_id,
    )

def _run_memory_update(session_id: str) -> None:
    db = SessionLocal()
    try:
        count = (
            db.query(SessionMessages)
            .filter(SessionMessages.session_id == session_id)
            .count()
        )

        if count % 10 == 0:
            logger.info(
                "Updating memory summary for session %s (msg_count=%s)",
                session_id,
                count,
            )
            update_memory_summary(db, session_id)
            db.commit()

    except Exception as e:
        logger.exception(
            "Background memory update failed for session %s: %s",
            session_id,
            e,
        )
    finally:
        db.close()

def shutdown_executor() -> None:
    """shutdown background workers.Call this on application shutdown."""
    global _AI_EXECUTOR
    if _AI_EXECUTOR:
        logger.info("Shutting down AI ProcessPoolExecutor")
        _AI_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _AI_EXECUTOR = None
