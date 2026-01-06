from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
import shutil
import tempfile
import os

from app.helpers import get_db, get_current_user
from app.schemas import DocumentSaveSchema

from app.services.document_service import (
    processDocument,
    createDocumentDraft,
    saveDocument,
)

router = APIRouter(prefix="/newdocuments", tags=["Documents"])


@router.get("/")
def greet():
    return {"status": "ok"}

@router.post("/upload")
async def uploadDocument(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    currentUser=Depends(get_current_user),
):
    tempPath = None

    try:
        # Save temp file
        suffix = os.path.splitext(file.filename)[1] or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tempPath = tmp.name

        if os.path.getsize(tempPath) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        fileSizeMb = os.path.getsize(tempPath) / (1024 * 1024)

        businessDocumentId = createDocumentDraft(
            db=db,
            tempFilePath=tempPath,
            originalFilename=file.filename,
            departmentId=currentUser.get("department_id"),
            currentUser=currentUser,
        )

        aiResult = processDocument(
            filePath=tempPath,
            documentId=businessDocumentId,
            filename=file.filename,
            fileType=file.content_type,
            fileSizeMb=fileSizeMb,
        )

        return {
            "status": "success",
            "documentId": businessDocumentId,
            "chunks": aiResult.get("chunks"),
            "ocrUsed": aiResult.get("ocr_used"),
            "fileSizeMb": round(fileSizeMb, 2),
        }

    finally:
        if tempPath and os.path.exists(tempPath):
            os.remove(tempPath)

@router.post("/{documentId}/save")
def saveDocumentApi(
    documentId: int,
    payload: DocumentSaveSchema,
    db: Session = Depends(get_db),
    currentUser=Depends(get_current_user),
):
    result = saveDocument(
        db=db,
        documentId=documentId,
        payload=payload,
        currentUser=currentUser,
    )

    return {
        "status": "success",
        "message": "Document submitted successfully",
        "data": result,
    }
