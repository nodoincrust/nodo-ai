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
    approve_document_step,
    reject_document_step,
    reupload_document_version,get_approver_inbox
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

    businessDocumentId = result["document_id"]
    permanentPath = result["file_path"]

    # try:
    #     aiResult = processDocument(
    #         filePath=permanentPath,
    #         documentId=businessDocumentId,
    #         filename=file.filename,
    #         fileType=file.content_type,
    #         fileSizeMb=fileSizeMb,
    #     )
    # except Exception as exc:
    #     raise HTTPException(status_code=500, detail="AI processing failed")

    background_tasks.add_task(
        processDocument,
        filePath=permanentPath,
        documentId=businessDocumentId,
        filename=file.filename,
        fileType=file.content_type,
        fileSizeMb=fileSizeMb,
    )
    return {
        "status": "success",
        "documentId": businessDocumentId,
        "filepath": permanentPath,
        "fileSizeMb": round(fileSizeMb, 2),
    }


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


@router.post("/approve/{document_id}")
def approve_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = approve_document_step(
        db=db,
        document_id=document_id,
        user_id=current_user["user_id"],
        current_user=current_user,
    )
    return {
        "statusCode": 200,
        "message": result["message"],
    }


@router.post("/{document_id}/reject")
def reject_document(
    document_id: int,
    payload: dict,  # Optional remark
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    remarks = payload.get("remarks") if payload else None

    result = reject_document_step(
        db=db,
        document_id=document_id,
        user_id=current_user["user_id"],
        remarks=remarks,
    )

    return {
        "statusCode": 200,
        "message": result["message"],
    }


@router.post("/{document_id}/reupload")
async def reupload_document(
    document_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # only uploader can reupload
    from app.models import Document

    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.uploaded_by == current_user["user_id"],
            Document.is_delete == False,
        )
        .first()
    )

    if not document:
        raise HTTPException(403, "Only uploader can reupload document")

    # save temp
    suffix = os.path.splitext(file.filename)[1] or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        temp_path = tmp.name

    if os.path.getsize(temp_path) == 0:
        os.remove(temp_path)
        raise HTTPException(400, "Uploaded file is empty")

    # permanent path
    company_id = current_user["company_id"]
    doc_dir = f"storage/companies/{company_id}/documents/{document_id}"
    os.makedirs(doc_dir, exist_ok=True)

    new_file_path = os.path.join(
        doc_dir, f"v{document.current_version + 1}_{file.filename}"
    )
    shutil.move(temp_path, new_file_path)

    result = reupload_document_version(
        db=db,
        document_id=document_id,
        file_path=new_file_path,
        file_name=file.filename,
        created_by=current_user["user_id"],
    )

    return {
        "statusCode": 200,
        "message": result["message"],
    }



@router.post("/approver/inbox")
def approver_inbox(
    search: str | None = None,
    page: int = 1,
    size: int = 10,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_approver_inbox(
        db=db,
        current_user=current_user,
        search=search,
        page=page,
        size=size,
    )
