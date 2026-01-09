from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
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
    get_document_full_details,
)

router = APIRouter(prefix="/nodo/newdocuments")


@router.get("/")
def greet():
    return {"status": "ok"}


@router.post("/upload")
async def uploadDocument(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    currentUser=Depends(get_current_user),
):
    suffix = os.path.splitext(file.filename)[1] or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tempPath = tmp.name

    if os.path.getsize(tempPath) == 0:
        os.remove(tempPath)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    fileSizeMb = os.path.getsize(tempPath) / (1024 * 1024)

    result = createDocumentDraft(
        db=db,
        tempFilePath=tempPath,
        originalFilename=file.filename,
        departmentId=currentUser.get("department_id"),
        currentUser=currentUser,
        )

    businessdocument_id = createDocumentDraft(
        db=db,
        tempFilePath=tempPath,
        originalFilename=file.filename,
        departmentId=currentUser.get("department_id"),
        currentUser=currentUser,
    )

    aiResult = processDocument(
        filePath=tempPath,
        document_id=businessdocument_id,
        filename=file.filename,
        fileType=file.content_type,
        fileSizeMb=fileSizeMb,
    )

    return {
        "status": "success",
        "document_id": businessdocument_id,
        "chunks": aiResult.get("chunks"),
        "ocrUsed": aiResult.get("ocr_used"),
        "fileSizeMb": round(fileSizeMb, 2),
    }


@router.post("/{document_id}/save")
def saveDocumentApi(
    document_id: int,
    payload: DocumentSaveSchema,
    db: Session = Depends(get_db),
    currentUser=Depends(get_current_user),
):
    result = saveDocument(
        db=db,
        document_id=document_id,
        payload=payload,
        currentUser=currentUser,
    )

    return {
        "status": "success",
        "message": "Draft metadata saved",
        "data": result,
    }


@router.get("/{document_id}/details")
def get_document_details(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return {
        "statusCode": 200,
        "data": get_document_full_details(
            db=db,
            document_id=document_id,
            current_user=current_user,
        ),
    }
