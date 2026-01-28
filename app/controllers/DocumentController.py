from fastapi import APIRouter, Request, UploadFile, File, Depends, HTTPException, BackgroundTasks,Form,Body
from fastapi.responses import FileResponse
from requests import request
from sqlalchemy.orm import Session
import shutil
import tempfile
import os
from app.models import Document
from app.helpers import FILE_TOKEN_STORE, get_db, get_current_user
from app.schemas import DocumentSaveSchema,GetApprovalDocumentList
from app.services.document_service import (
    details_editor,
    processDocument,
    createDocumentDraft,
    saveDocument,
    get_document_full_details,
    approve_document_step,
    reject_document_step,
    reupload_document_version,get_approver_inbox,
)
from app.services.BouquetService import(
    createBouquet,getBouquetById,appendDocumentToBouquet,removeDocumentFromBouquet,deleteBouquet,getAllBoqList
)
import logging
logger = logging.getLogger("document.controller")
 
router = APIRouter(prefix="/nodo/newdocuments")
 
 
@router.get("/")
def greet():
    return {"status": "ok"}
 
 
@router.post("/upload")
async def uploadDocument(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    documentId: int | None = Form(None),   # <--- ADDED
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
 
    # === REUPLOAD CASE ===
    if documentId:
        document = db.query(Document).filter(
            Document.id == documentId,
            Document.is_delete.is_(False)
        ).first()
 
        if document and document.status == "REJECTED":
            newVersion = reupload_document_version(
                db=db,
                document_id=document.id,
                file_path=tempPath,
                file_name=file.filename,
                created_by=currentUser["user_id"],
            )
 
            # AI Processing for v2
            background_tasks.add_task(
                processDocument,
                filePath=tempPath,
                documentId=document.id,
                versionId=newVersion["version_id"],
                filename=file.filename,
                fileType=file.content_type,
                fileSizeMb=fileSizeMb,
            )
 
            return {
                "status": "success",
                "documentId": document.id,
                "version": newVersion["version"],
                "version_id": newVersion["version_id"],
                "filepath": newVersion["file_path"]
            }
 
    # === NORMAL NEW DOCUMENT FLOW ===
    result = createDocumentDraft(
        db=db,
        tempFilePath=tempPath,
        originalFilename=file.filename,
        departmentId=currentUser.get("department_id"),
        currentUser=currentUser,
    )
 
    businessDocumentId = result["document_id"]
    permanentPath = result["file_path"]
    versionId = result["version_id"]
 
    background_tasks.add_task(
        processDocument,
        filePath=permanentPath,
        documentId=businessDocumentId,
        versionId=versionId,
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
    version: int | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    details = get_document_full_details(
            db=db,
            document_id=document_id,
            version=version,
            current_user=current_user,
        ),
    details = details_editor(
        details=details,
        current_user=current_user,
    ) 
    return {
        "statusCode": 200,
        "data" : details,
        # "data": get_document_full_details(
        #     db=db,
        #     document_id=document_id,
        #     version=version,
        #     current_user=current_user,
        # ),
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
 
 
@router.post("/reject/{document_id}")
def reject_document(
    document_id: int,
    payload:dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    remarks = payload.get("reason") if payload else None
    print("remarks-----------",remarks)
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
 
 
@router.post("/reupload/{document_id}")
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
    payload:GetApprovalDocumentList,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    print("asdfghjkl",payload.dict())
    return get_approver_inbox(
        db=db,
        current_user=current_user,
        search=payload.search,
        status=payload.status,
        page=payload.page,
        pagelimit=payload.pagelimit
    )
 
 
 
@router.post("/bouquets")
def createBouquetEndpoint(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    currentUser: dict = Depends(get_current_user),
):
    name = payload.get("name")
    description = payload.get("description")
 
    if not name:
        raise HTTPException(400, "name is required")
 
    bouquet = createBouquet(
        db=db,
        name=name,
        description=description,
        createdBy=currentUser["user_id"],
    )
 
    return {
        "id": bouquet.id,
        "message": "Bouquet created successfully",
    }
 
@router.get("/getAllBoq")
def getAllBoq( db: Session = Depends(get_db),currentUser: dict = Depends(get_current_user)):
   
    result= getAllBoqList(db=db,current_user=currentUser)
   
    if not result:
        raise HTTPException("Boq not found")
    else:
        return result
   
   
@router.get("/bouquets/{bouquetId}")
def getBouquet(
    bouquetId: int,
    db: Session = Depends(get_db),
):
    result = getBouquetById(db, bouquetId)
 
    if not result:
        raise HTTPException(404, "Bouquet not found")
 
    return result
 
 
@router.delete("/bouquets/{bouquetId}")
def deleteBouquetEndpoint(
    bouquetId: int,
    db: Session = Depends(get_db),
    currentUser: dict = Depends(get_current_user),
):
    deleteBouquet(
        db=db,
        bouquetId=bouquetId,
        currentUserId=currentUser["user_id"],
    )
    return {"message": "Bouquet deleted successfully"}
 
 
@router.post("/bouquets/{bouquetId}/appendDocument")
def appendDocument(
    bouquetId: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    documentId = payload.get("documentId")
 
    if not documentId:
        raise HTTPException(400, "documentId is required")
 
    appendDocumentToBouquet(
        db=db,
        bouquetId=bouquetId,
        documentId=documentId,
    )
 
    return {"message": "Document appended to bouquet successfully"}
 
 
@router.delete("/bouquets/{bouquetId}/removeDocument")
def removeDocument(
    bouquetId: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    documentId = payload.get("documentId")
 
    if not documentId:
        raise HTTPException(400, "documentId is required")
 
    removeDocumentFromBouquet(
        db=db,
        bouquetId=bouquetId,
        documentId=documentId,
    )
 
    return {"message": "Document removed from bouquet successfully"}

@router.api_route("/internal/onlyoffice/file/{token}", methods=["GET", "POST", "HEAD"])
def stream_file_by_token(token: str):
    logger.info(f"Request received for token: {token}, method: {request.method}")  # *** ADD: Log the request method ***
    meta = FILE_TOKEN_STORE.get(token)
    if not meta:
        logger.warning(f"Invalid token: {token}")  # *** ADD: Log invalid token ***
        raise HTTPException(status_code=403)

    file_path = meta["file_path"].lstrip("/")

    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")  # *** ADD: Log missing file ***
        raise HTTPException(status_code=404)

    logger.info(f"Serving file: {file_path}")  # *** ADD: Log successful serve ***
    return FileResponse(
        path=file_path,
        filename=os.path.basename(file_path),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{os.path.basename(file_path)}"',
        }
    )

@router.post("/onlyoffice/callback/{document_id}")
async def onlyoffice_callback(document_id: int, request: Request):
    body = await request.json()
    logger.info(f"OnlyOffice callback for doc {document_id}: {body}")
    
    status = body.get("status")
    if status == 2:
        changed_url = body.get("url")
        return {"error": 0}  # Success response to OnlyOffice
    
    return {"error": 0}  # Acknowledge

@router.api_route("/internal/onlyoffice/storage/{full_path:path}", methods=["GET", "HEAD"])
def onlyoffice_stream(full_path: str):
    
    safe_root = os.path.abspath("storage")
    abs_path = os.path.abspath(os.path.join("storage", full_path.replace("storage/", "")))

    if not abs_path.startswith(safe_root):
        raise HTTPException(status_code=403, detail="Invalid path")

    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=abs_path,
        filename=os.path.basename(abs_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )