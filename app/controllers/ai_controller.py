import uuid
import shutil
import os
from typing import Optional
import tempfile
import traceback

from fastapi import (APIRouter,UploadFile,File,Form,HTTPException)
from fastapi.responses import StreamingResponse
from app.db import SessionLocal
from app.models import Document
from app.schemas import ChatRequest, CitationRequest
from app.AIhelpers.ai_helper import (handle_chat,handle_chat_stream,handle_summary)
from app.services.ai_DBservice import create_chat_session
from app.services.document_service import process_document

router = APIRouter(prefix="/ai")

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None)
):
    import tempfile
    import os
    import shutil
    import uuid

    try:
        # preserve original extension so downstream format detection works correctly
        _, ext = os.path.splitext(file.filename or '')
        if not ext:
            # fallback to using content-type hint
            if file.content_type:
                if 'pdf' in file.content_type:
                    ext = '.pdf'
                elif 'word' in file.content_type:
                    ext = '.docx'
                elif 'excel' in file.content_type:
                    ext = '.xlsx'
                elif 'image' in file.content_type:
                    # default to jpg for generic images
                    ext = '.jpg' or '.png' or '.jpeg' or '.webp'
                else:
                    ext = ''

        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            shutil.copyfileobj(file.file, tmp)
            temp_path = tmp.name

        # VERIFY FILE IS NOT EMPTY
        if os.path.getsize(temp_path) == 0:
            raise ValueError("Uploaded file is empty")

        document_id = str(uuid.uuid4())
        file_size_mb = os.path.getsize(temp_path) / (1024 * 1024)

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

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        #CLEANUP TEMP FILE
        try:
            os.remove(temp_path)
        except Exception:
            pass

@router.post("/chat")
async def chat_api(request: ChatRequest):
    session_id = request.session_id or create_chat_session()

    if request.document_id:
        validate_document(request.document_id)

    if request.document_id and not request.query:
        summary = handle_summary(request.document_id)
        return {
            "session_id": session_id,
            "mode": "document_summary",
            "response": summary["summary"]
        }
    result = handle_chat(
        session_id=session_id,
        query=request.query,
        document_id=request.document_id
    )

    return {
        "session_id": session_id,
        "mode": "rag" if request.document_id else "chat",
        "response": result["answer"],
        "citations": result["citations"]
    }


@router.post("/chat/stream")
async def chat_stream_api(request: ChatRequest):
   
    session_id = request.session_id or create_chat_session()

    return StreamingResponse(
        handle_chat_stream(
            session_id=session_id,
            query=request.query
        ),
        media_type="text/plain"
    )

@router.get("/summary/{document_id}")
async def summarize_document(document_id: str):
    # Generate and return a document summary.
    return handle_summary(document_id)