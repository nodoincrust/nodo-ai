import shutil
import os
import tempfile
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
    Form,
    Depends,
)
from app.db import SessionLocal
from app.helpers import get_current_user
from app.models import Document
from app.services.document_service import processDocument, createDocumentDraft
from app.services.chat_service import chatWithDocument
from app.services.summary_service import summarizeDocument
from app.services.ai_DBservice import getOrCreateSessionForDocument

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


@router.post("/chat")
async def chatApi(*, documentId: int, query: str):
    """
    Document-anchored chat.

    - Same document → same session
    - Exactly ONE LLM call
    """
    if not documentId:
        raise HTTPException(status_code=400, detail="documentId is required")

    sessionId = getOrCreateSessionForDocument(documentId)

    result = chatWithDocument(
        documentId=documentId,
        sessionId=sessionId,
        query=query,
    )

    return {
        "documentId": documentId,
        "sessionId": sessionId,
        "answer": result["answer"],
        "citations": result.get("citations", []),
    }


# @router.get("/summary/{documentId}")
# def summarizeApi(documentId: int):

#     getOrCreateSessionForDocument(documentId)

#     return summarizeDocument(documentId)

from fastapi.concurrency import run_in_threadpool

@router.get("/summary/{documentId}")
async def summarizeApi(documentId: int):
    # ensure session exists
    getOrCreateSessionForDocument(documentId)

    #  RUN IN WORKER THREAD, WAIT FOR RESULT
    result = await run_in_threadpool(
        summarizeDocument,
        documentId,
    )

    return result
