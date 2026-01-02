import uuid
import shutil
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse

from app.services.document_service import process_document
from app.services.chat_service import chat_with_session, chat_stream
from app.services.summary_service import summarize_doc
from app.services.ai_DBservice import create_chat_session

ASYNC_THRESHOLD_MB = 2.0

router = APIRouter(prefix="/ai")

@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
):
    #Create temp file (STREAM SAFE)
    _, ext = os.path.splitext(file.filename or "")
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        temp_path = tmp.name

    if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
        raise HTTPException(400, "Empty upload")

    file_size_mb = os.path.getsize(temp_path) / (1024 * 1024)
    document_id = str(uuid.uuid4())

    # 2️⃣ Async vs sync decision
    if file_size_mb >= ASYNC_THRESHOLD_MB:
        background_tasks.add_task(
            process_document,
            file_path=temp_path,
            document_id=document_id,
            filename=file.filename,
            session_id=session_id,
            file_type=file.content_type,
            file_size_mb=file_size_mb,
        )

        return {
            "status": "processing",
            "document_id": document_id,
            "file_size_mb": round(file_size_mb, 2),
        }

    #Small file → sync
    try:
        result = process_document(
            file_path=temp_path,
            document_id=document_id,
            filename=file.filename,
            session_id=session_id,
            file_type=file.content_type,
            file_size_mb=file_size_mb,
        )

        return {
            "status": "success",
            "document_id": document_id,
            "chunks": result["chunks"],
            "ocr_used": result["ocr_used"],
            "file_size_mb": round(file_size_mb, 2),
        }

    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass

@router.post("/chat")
async def chat_api(query: str, session_id: Optional[str] = None):
    session_id = session_id or create_chat_session()

    result = chat_with_session(
        session_id=session_id,
        query=query,
    )

    return {
        "session_id": session_id,
        "response": result["answer"],
        "citations": result.get("citations", []),
    }


@router.post("/chat/stream")
async def chat_stream_api(query: str, session_id: Optional[str] = None):
    session_id = session_id or create_chat_session()

    return StreamingResponse(
        chat_stream(session_id=session_id, query=query),
        media_type="text/plain",
    )

@router.get("/summary/{document_id}")
def summarize_document(document_id: str):
    """
    Cached, safe, ONE LLM CALL MAX
    """
    return summarize_doc(document_id)
