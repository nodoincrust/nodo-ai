from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.orm import Session
import shutil
import tempfile
import os
import uuid

from app.helpers import get_db
from app.services.document_service import create_document_draft,save_document,process_document

from app.helpers import get_current_user
from app.schemas import DocumentSaveSchema

router = APIRouter(prefix="/newdocuments", tags=["Documents"])


@router.get("/")
def greet():
    return "Hello Dept"

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            shutil.copyfileobj(file.file, tmp)
            temp_path = tmp.name

        if os.path.getsize(temp_path) == 0:
            raise HTTPException(400, "Uploaded file is empty")

        document_id = str(uuid.uuid4())
        file_size_mb = os.path.getsize(temp_path) / (1024 * 1024)

        ai_result = process_document(
            file_path=temp_path,
            document_id=document_id,
            filename=file.filename,
            session_id=None,
            file_type=file.content_type,
            file_size_mb=file_size_mb,
        )

        # ✅ create business draft
        business_document_id = create_document_draft(
            db=db,
            ai_document_id=document_id,
            temp_file_path=temp_path,
            original_filename=file.filename,
            department_id=current_user.get("department_id"),
            current_user=current_user,
        )

        return {
            "status": "success",
            "document_id": business_document_id,
            "ai_document_id": document_id,
            "chunks": ai_result.get("chunks"),
            "ocr_used": ai_result.get("ocr_used"),
            "file_size_mb": round(file_size_mb, 2),
        }

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)



@router.post("/{document_id}/save")
def save_document_api(
    document_id: int,
    payload: DocumentSaveSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = save_document(
        db=db,
        document_id=document_id,
        payload=payload,
        current_user=current_user,
    )

    return {
        "status": "success",
        "message": "Document submitted successfully",
        "data": result,
    }