import logging
import asyncio
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from app.db import SessionLocal
from app.models import SessionMessage
from app.services.memory_service import pruneOldMessages, updateMemorySummary

logger = logging.getLogger("ai.backgroundTasks")

MEMORY_UPDATE_INTERVAL = 10  
MAX_WORKERS = 4  

_executor: Optional[ThreadPoolExecutor] = None


def getExecutor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    return _executor  

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
    )  


def runMemoryUpdate(sessionId: str) -> None:
    db = SessionLocal()
    try:
        messageCount = db.query(SessionMessage).filter_by(session_id=sessionId).count()

        if messageCount == 0 or messageCount % MEMORY_UPDATE_INTERVAL != 0:
            return

        updated = updateMemorySummary(
            db,
            sessionId=sessionId,
            messageCount=messageCount,
        )

       
        if updated:
            deleted = pruneOldMessages(
                db,
                sessionId=sessionId,
                keep_last=50,
            )

            logger.info(
                "Session %s → memory updated, %s messages pruned",
                sessionId,
                deleted,
            )

        db.commit()

    except Exception:
        logger.exception(
            "Background memory update failed for session %s",
            sessionId,
        )
        db.rollback()

    finally:
        db.close()  


def shutdownExecutor() -> None:
    global _executor
    if _executor:
        logger.info("Shutting down background executor")
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None  
