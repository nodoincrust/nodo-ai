import os
import shutil
import tempfile
from uuid import uuid4
from threading import Thread

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
    Depends,
)

from app.db import SessionLocal
from app.helpers import get_current_user, run_summary_job
from app.services.document_service import processDocument, createDocumentDraft
from app.services.chat_service import chatWithDocument
from app.services.ai_DBservice import getOrCreateSessionForDocument
from jobs_store import jobs

ASYNC_THRESHOLD_MB = 2.0                                  # File size threshold for async processing

router = APIRouter(prefix="/nodo/ai")


@router.post("/upload")
async def uploadDocument(
    backgroundTasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")  # Validates filename

    tempPath = None
    db = SessionLocal()

    try:
        _, extension = os.path.splitext(file.filename)

        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tempPath = tmp.name                                           # Stores uploaded file temporarily

        fileSizeMb = os.path.getsize(tempPath) / (1024 * 1024)

        documentId = createDocumentDraft(
            db=db,
            tempFilePath=tempPath,
            originalFilename=file.filename,
            departmentId=current_user["department_id"],
            currentUser=current_user,
        )                                                                 # Creates draft document

        if fileSizeMb >= ASYNC_THRESHOLD_MB:
            backgroundTasks.add_task(
                processDocument,
                filePath=tempPath,
                document_id=documentId,
                filename=file.filename,
                fileType=file.content_type,
                fileSizeMb=fileSizeMb,
            )
            return {
                "status": "processing",
                "documentId": documentId,
                "fileSizeMb": round(fileSizeMb, 2),
            }                                                             # Triggers async ingestion

        result = processDocument(
            filePath=tempPath,
            document_id=documentId,
            filename=file.filename,
            fileType=file.content_type,
            fileSizeMb=fileSizeMb,
        )                                                                 # Processes small files synchronously

        sessionId = getOrCreateSessionForDocument(documentId)

        return {
            "status": "success",
            "documentId": documentId,
            "sessionId": sessionId,
            "chunks": result.get("chunks", 0),
            "ocr_used": result.get("ocr_used", False),
            "fileSizeMb": round(fileSizeMb, 2),
        }

    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))             # Handles upload failure

    finally:
        db.close()
        if tempPath and os.path.exists(tempPath):
            os.remove(tempPath)                                           # Cleans up temporary file


@router.get("/chat")
def chatApi(*, document_id: int, query: str):
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id is required")  # Validates input

    session_id = getOrCreateSessionForDocument(document_id)

    result = chatWithDocument(
        document_id=document_id,
        session_id=session_id,
        query=query,
    )                                                                     # Executes chat pipeline

    return {
        "document_id": document_id,
        "session_id": session_id,
        "answer": result["answer"],
        "citations": result.get("citations", []),
    }


@router.post("/summary/start/{documentId}")
def start_summary(documentId: int):
    job_id = uuid4().hex
    jobs[job_id] = {"status": "running", "result": None}                  # Registers summary job

    getOrCreateSessionForDocument(documentId)

    Thread(
        target=run_summary_job,
        args=(job_id, documentId),
        daemon=True,
    ).start()                                                              # Runs summary generation in background

    return {"job_id": job_id}


@router.get("/summary/status/{job_id}")
def get_summary_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "not_found"}                                    # Handles invalid job lookup
    return {"job_id": job_id, **job}