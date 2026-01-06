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
)
from app.db import SessionLocal
from app.models import Document
from app.services.document_service import processDocument
from app.services.chat_service import chatWithDocument
from app.services.summary_service import summarizeDocument
from app.services.ai_DBservice import getOrCreateSessionForDocument

ASYNC_THRESHOLD_MB = 2.0

router = APIRouter(prefix="/ai")


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

    # --------------------------------------------------
    # 1️⃣ SAVE TEMP FILE
    # --------------------------------------------------
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
        document = Document(
            company_id=3,        # ✅ DEFAULT
            department_id=1,     # ✅ DEFAULT
            uploaded_by=3,       # ✅ DEFAULT
            status="DRAFT",
            current_version=1,
            is_active=True,
            is_delete=False,
        )

        db.add(document)
        db.commit()
        db.refresh(document)

        documentId = document.id  # ✅ SAFE INT

        # --------------------------------------------------
        # 3️⃣ PROCESS DOCUMENT
        # --------------------------------------------------
        if fileSizeMb >= ASYNC_THRESHOLD_MB:
            backgroundTasks.add_task(
                processDocument,
                filePath=tempPath,
                documentId=documentId,
                filename=file.filename,
                fileType=file.content_type,
                fileSizeMb=fileSizeMb,
            )

            return {
                "status": "processing",
                "documentId": documentId,
                "fileSizeMb": round(fileSizeMb, 2),
            }

        result = processDocument(
            filePath=tempPath,
            documentId=documentId,
            filename=file.filename,
            fileType=file.content_type,
            fileSizeMb=fileSizeMb,
        )
        sessionId = getOrCreateSessionForDocument(documentId)

        return {
            "status": "success",
            "documentId": documentId,
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

# ======================================================
# CHAT API
# ======================================================
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


# ======================================================
# SUMMARY API
# ======================================================
@router.get("/summary/{documentId}")
def summarizeApi(documentId: int):

    getOrCreateSessionForDocument(documentId)

    return summarizeDocument(documentId)
