import logging
import asyncio
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from app.db import SessionLocal
from app.models import SessionMessage, SessionMemorySummary
from app.services.memory_service import updateMemorySummary

logger = logging.getLogger("ai.backgroundTasks")

MEMORY_UPDATE_INTERVAL = 10                             # Triggers memory update every N messages
MAX_WORKERS = 4                                         # Limits background thread usage

_executor: Optional[ThreadPoolExecutor] = None


def getExecutor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    return _executor                                    # Lazily initializes executor


def submitMemoryUpdate(sessionId: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        runMemoryUpdate(sessionId)
        return

    loop.run_in_executor(
        getExecutor(),
        runMemoryUpdate,
        sessionId,
    )                                                    # Schedules memory update asynchronously


def runMemoryUpdate(sessionId: str) -> None:
    db = SessionLocal()
    try:
        messageCount = (
            db.query(SessionMessage)
            .filter_by(session_id=sessionId)
            .count()
        )

        if messageCount == 0 or messageCount % MEMORY_UPDATE_INTERVAL != 0:
            return                                      # Skips update unless threshold reached

        existing = (
            db.query(SessionMemorySummary)
            .filter_by(session_id=sessionId)
            .first()
        )

        if existing and existing.updated_at:
            return                                      # Prevents duplicate updates

        logger.info(
            "Updating memory summary session=%s messages=%s",
            sessionId,
            messageCount,
        )

        updateMemorySummary(
            db,
            sessionId=sessionId,
            messageCount=messageCount,
        )                                                # Generates summarized memory
        db.commit()

    except Exception:
        logger.exception(
            "Background memory update failed for session %s",
            sessionId,
        )
        db.rollback()

    finally:
        db.close()                                      # Ensures DB session cleanup


def shutdownExecutor() -> None:
    global _executor
    if _executor:
        logger.info("Shutting down background executor")
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None                                # Gracefully shuts down threads