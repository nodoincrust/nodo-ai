import uuid
import shutil
import os
from typing import Optional
import tempfile
import traceback
import tempfile
import os
import shutil
import uuid

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas import ChatRequest, CitationRequest
from app.AIhelpers.ai_helper import (
    handle_chat,
    handle_chat_stream,
    handle_chat_with_citation,
    handle_summary,
)
from app.services.ai_DBservice import create_chat_session
from app.services.document_service import process_document

router = APIRouter(prefix="/ai")


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...), session_id: Optional[str] = Form(None)
):
    print("reached in ai controller")

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
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
        # CLEANUP TEMP FILE
        try:
            os.remove(temp_path)
        except Exception:
            pass


@router.post("/chat")
async def chat_api(request: ChatRequest):
    session_id = request.session_id or create_chat_session()

    result = handle_chat(session_id=session_id, query=request.query)

    return {
        "session_id": session_id,
        "response": result["data"]["answer"],
        "citations": result["data"].get("citations", []),
    }


@router.get("/summary/{document_id}")
def summarize_document(document_id: str):
    # Generate and return a document summary.
    return handle_summary(document_id)
