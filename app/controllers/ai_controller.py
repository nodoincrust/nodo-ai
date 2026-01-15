import shutil
import os
import tempfile
from app.helpers import get_db
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
    Form,
    Query,
    Depends,
)
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.helpers import get_current_user,run_summary_job
from app.models import Document,DocumentVersion
from app.services.document_service import processDocument, createDocumentDraft
from app.services.chat_service import chatWithDocument
from app.services.summary_service import summarizeDocument
from app.services.ai_DBservice import getOrCreateSessionForDocument


from fastapi.concurrency import run_in_threadpool
from uuid import uuid4
from threading import Thread

from jobs_store import jobs

ASYNC_THRESHOLD_MB = 2.0

router = APIRouter(prefix="/nodo/ai")


@router.post("/upload")
async def uploadDocument(
    backgroundTasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    print(current_user)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    _, extension = os.path.splitext(file.filename)

    with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tempPath = tmp.name

    if not os.path.exists(tempPath) or os.path.getsize(tempPath) == 0:
        os.remove(tempPath)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    fileSizeMb = os.path.getsize(tempPath) / (1024 * 1024)

    db = SessionLocal()
    try:
        businessDocumentId = createDocumentDraft(
            db=db,
            tempFilePath=tempPath,
            originalFilename=file.filename,
            departmentId=current_user.get("department_id"),
            currentUser=current_user,
        )

        if fileSizeMb >= ASYNC_THRESHOLD_MB:
            backgroundTasks.add_task(
                processDocument,
                filePath=tempPath,
                documentId=businessDocumentId,
                filename=file.filename,
                fileType=file.content_type,
                fileSizeMb=fileSizeMb,
            )

            return {
                "status": "processing",
                "documentId": businessDocumentId,
                "fileSizeMb": round(fileSizeMb, 2),
            }

        result = processDocument(
            filePath=tempPath,
            documentId=businessDocumentId,
            filename=file.filename,
            fileType=file.content_type,
            fileSizeMb=fileSizeMb,
        )
        sessionId = getOrCreateSessionForDocument(businessDocumentId)

        return {
            "status": "success",
            "documentId": businessDocumentId,
            "sessionId": sessionId,
            "chunks": result.get("chunks", 0),
            "ocrUsed": result.get("ocr_used", False),
            "fileSizeMb": round(fileSizeMb, 2),
        }

    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        db.close()
        try:
            os.remove(tempPath)
        except Exception:
            pass

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
 
 

# @router.get("/summary/{documentId}")
# def summarizeApi(documentId: int):

#     getOrCreateSessionForDocument(documentId)

#     return summarizeDocument(documentId)

@router.post("/summary/start/{documentId}")
def start_summary(documentId: int,version: int = Query(...)):
    job_id = uuid4().hex
    jobs[job_id] = {"status": "running", "result": None}

    # ensure session exists
    getOrCreateSessionForDocument(documentId,version)

    thread = Thread(target=run_summary_job, args=(job_id, documentId,version), daemon=True)
    thread.start()

    return {"job_id": job_id}


@router.get("/summary/status/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "not_found"}
    
    return {
        "job_id": job_id,
        **job
    }