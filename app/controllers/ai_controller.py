import shutil
import os
import tempfile

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
)
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Document
from app.services.document_service import processDocument
from app.services.chat_service import chatWithDocument
from app.services.summary_service import summarizeDocument
from app.services.ai_DBservice import getOrCreateSessionForDocument

ASYNC_THRESHOLD_MB = 2.0

router = APIRouter(prefix="/ai", tags=["AI"])


# ======================================================
# UPLOAD DOCUMENT
# ======================================================
@router.post("/upload")
async def uploadDocument(
    backgroundTasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    tempPath = None
    db: Session = SessionLocal()

    try:
        # --------------------------------------------------
        # 1️⃣ SAVE TEMP FILE
        # --------------------------------------------------
        _, extension = os.path.splitext(file.filename)
        extension = extension or ".pdf"

        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tempPath = tmp.name

        if not os.path.exists(tempPath) or os.path.getsize(tempPath) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        fileSizeMb = os.path.getsize(tempPath) / (1024 * 1024)

        # --------------------------------------------------
        # 2️⃣ CREATE BUSINESS DOCUMENT
        # --------------------------------------------------
        document = Document(
            company_id=3,        # TODO: make dynamic later
            department_id=1,
            uploaded_by=3,
            status="DRAFT",
            current_version=1,
            is_active=True,
            is_delete=False,
        )

        db.add(document)
        db.commit()
        db.refresh(document)

        document_id = document.id

        # --------------------------------------------------
        # 3️⃣ PROCESS DOCUMENT (ASYNC / SYNC)
        # --------------------------------------------------
        if fileSizeMb >= ASYNC_THRESHOLD_MB:
            backgroundTasks.add_task(
                processDocument,
                filePath=tempPath,
                document_id=document_id,
                filename=file.filename,
                fileType=file.content_type,
                fileSizeMb=fileSizeMb,
            )

            return {
                "status": "processing",
                "document_id": document_id,
                "file_size_mb": round(fileSizeMb, 2),
            }

        result = processDocument(
            filePath=tempPath,
            document_id=document_id,
            filename=file.filename,
            fileType=file.content_type,
            fileSizeMb=fileSizeMb,
        )

        # --------------------------------------------------
        # 4️⃣ FETCH SESSION (READ-ONLY)
        # --------------------------------------------------
        session_id = getOrCreateSessionForDocument(document_id)

        return {
            "status": "success",
            "document_id": document_id,
            "session_id": session_id,
            "chunks": result.get("chunks", 0),
            "ocr_used": result.get("ocr_used", False),
            "file_size_mb": round(fileSizeMb, 2),
        }

    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        db.close()
        if tempPath and os.path.exists(tempPath):
            os.remove(tempPath)


# ======================================================
# CHAT API
# ======================================================
@router.post("/chat")
async def chatApi(
    *,
    document_id: int,
    query: str,
):
    """
    Document-anchored chat.

    - One document → one session
    - Works even if /summary is never called
    """
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id is required")

    # --------------------------------------------------
    # 1️⃣ FETCH SESSION (READ-ONLY)
    # --------------------------------------------------
    session_id = getOrCreateSessionForDocument(document_id)

    # --------------------------------------------------
    # 2️⃣ CHAT
    # --------------------------------------------------
    result = chatWithDocument(
        document_id=document_id,
        session_id=session_id,
        query=query,
    )

    return {
        "document_id": document_id,
        "session_id": session_id,
        "answer": result["answer"],
        "citations": result.get("citations", []),
    }


# ======================================================
# SUMMARY API
# ======================================================
@router.get("/summary/{document_id}")
def summarizeApi(document_id: int):
    """
    Document summary.

    - Uses ONLY document_chunks
    - Generates summary + tags
    - Safe to call anytime
    """
    # Ensure session exists (read-only check)
    getOrCreateSessionForDocument(document_id)

    return summarizeDocument(document_id)
