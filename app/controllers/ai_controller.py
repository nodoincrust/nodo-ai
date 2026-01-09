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
from app.services.chat_service import chatWithDocument, fetchAllMessages
from app.services.summary_service import summarizeDocument
from app.services.ai_DBservice import getOrCreateSessionForDocument

ASYNC_THRESHOLD_MB = 5.0 # Files larger than this will be processed asynchronously

router = APIRouter(prefix="/ai", tags=["AI"])

#upload document endpoint
@router.post("/upload")
async def uploadDocument(
    backgroundTasks: BackgroundTasks,
    file: UploadFile = File(...), #fastapi UploadFile object : file
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    tempPath = None
    db: Session = SessionLocal()

    try:
        # Save uploaded file to a temporary location and handel empty extension.
        _, extension = os.path.splitext(file.filename)
        extension = extension or ".pdf"
        # Create a temporary file obj and 
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tempPath = tmp.name
        # Validate file exists in temp path and is not empty 
        if not os.path.exists(tempPath) or os.path.getsize(tempPath) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        fileSizeMb = os.path.getsize(tempPath) / (1024 * 1024)
        # creating documnet records in db using Document model
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

        document_id = document.id #shows the record id of document created

        #process document in background if size exceeds threshold.The ASync processing is done using FastAPI BackgroundTasks
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

        # process document synchronously for smaller files
        result = processDocument(
            filePath=tempPath,
            document_id=document_id,
            filename=file.filename,
            fileType=file.content_type,
            fileSizeMb=fileSizeMb,
        )

        # getOrCreateSessionForDocument to fetch or create a session for the document
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

    # written finally block to ensure db session is closed and temporary file is deleted
    finally:
        db.close()
        if tempPath and os.path.exists(tempPath):
            os.remove(tempPath)

# chat api endpoint
@router.get("/chat")
async def chatApi(
    *,
    document_id: int,
    query: str,
):
    if not document_id:
        raise HTTPException(status_code=400, detail="document_id is required")
    
    # ensure session exists for document
    session_id = getOrCreateSessionForDocument(document_id)

    #Calls chat pipeline that retrieves context, calls the LLM and returns the answer
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

#summarize api endpoint
@router.get("/summary/{document_id}")
def summarizeApi(document_id: int):

    # Ensure session exists for document
    getOrCreateSessionForDocument(document_id)
    #call summarizeDocument function
    return summarizeDocument(document_id)

#chat History api
@router.get("/chat/{documentId}/history")
def getChatHistory(documentId: int):
    sessionId = getOrCreateSessionForDocument(documentId)
    db = SessionLocal()
    try:
        messages = fetchAllMessages(db, sessionId=sessionId)
        return {
            "documentId": documentId,
            "sessionId": sessionId,
            "messages": [
                {
                    "id": str(m.id),
                    "role": m.role,
                    "content": m.content,
                    "createdAt": m.created_at.isoformat(),
                }
                for m in messages
            ],
        }
    finally:
        db.close()
