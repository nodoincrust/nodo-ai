from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Depends,
    HTTPException,
    BackgroundTasks,
    Form,
    Body,
)
from sqlalchemy.orm import Session
import shutil
import tempfile
import os
from app.models import Document
from app.helpers import get_db, get_current_user
from app.schemas import (
    DocumentSaveSchema,
    GetApprovalDocumentList,
    createBouquetSchema,
    BoqFilter,
    DocFilter,
    updateBouquet,
    AppendDocumentsSchema,
    BoqDocsFilter,
    RemoveDocumentsSchema,
)
from app.services.document_service import (
    processDocument,
    createDocumentDraft,
    saveDocument,
    get_document_full_details,
    approve_document_step,
    reject_document_step,
    reupload_document_version,
    get_approver_inbox,
)
from app.services.bouquetService import (
    createBouquet,
    getBouquetById,
    removeDocumentFromBouquet,
    deleteBouquet,
    getAllBoqList,
    get_approved_documents_service,
    update_boq_details,
    append_documents_to_bouquet,
    get_bouquet_documents_service,
)

router = APIRouter(prefix="/nodo/newdocuments")


@router.get("/")
def greet():
    return {"status": "ok"}


@router.post("/upload")
async def uploadDocument(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    documentId: int | None = Form(None),  # <--- ADDED
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
        document = (
            db.query(Document)
            .filter(Document.id == documentId, Document.is_delete.is_(False))
            .first()
        )

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
                "filepath": newVersion["file_path"],
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
    return {
        "statusCode": 200,
        "data": get_document_full_details(
            db=db,
            document_id=document_id,
            version=version,
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


@router.post("/reject/{document_id}")
def reject_document(
    document_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    remarks = payload.get("reason") if payload else None
    print("remarks-----------", remarks)
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
    payload: GetApprovalDocumentList,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    print("asdfghjkl", payload.dict())
    return get_approver_inbox(
        db=db,
        current_user=current_user,
        search=payload.search,
        status=payload.status,
        page=payload.page,
        pagelimit=payload.pagelimit,
    )


@router.post("/createBouquet")
def createBouquetEndpoint(
    payload: createBouquetSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return createBouquet(
        db=db,
        name=payload.name,
        description=payload.description,
        current_user=current_user,
    )


@router.post("/getAllBoq")
def getAllBoq(
    filters: BoqFilter,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return getAllBoqList(db=db, current_user=current_user, filters=filters)


@router.post("/updateBouquet/{bouquetId}")
def update_bouquet(
    bouquetId: int,
    payload: updateBouquet,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return update_boq_details(
        db=db, current_user=current_user, payload=payload, bouquetId=bouquetId
    )


@router.get("/bouquets/{bouquetId}")
def getBouquet(
    bouquetId: int,
    db: Session = Depends(get_db),
):
    result = getBouquetById(db, bouquetId)

    if not result:
        raise HTTPException(404, "Bouquet not found")

    return result


@router.delete("/deleteBouquet/{bouquetId}")
def deleteBouquetEndpoint(
    bouquetId: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return deleteBouquet(
        db=db,
        bouquetId=bouquetId,
        current_user=current_user,
    )


@router.post("/boqDocuments/{bouquetId}")
def get_bouquet_documents(
    bouquetId: int,
    filters: BoqDocsFilter,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return get_bouquet_documents_service(
        db=db, current_user=current_user, bouquetId=bouquetId, filters=filters
    )


@router.post("/appendDocuments/{bouquetId}")
def append_documents(
    bouquetId: int,
    payload: AppendDocumentsSchema,
    db: Session = Depends(get_db),
):
    return append_documents_to_bouquet(
        db=db, bouquetId=bouquetId, documentIds=payload.documentIds
    )


@router.delete("/removeDocuments/{bouquetId}")
def remove_documents(
    bouquetId: int,
    payload: RemoveDocumentsSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return removeDocumentFromBouquet(
        db=db,
        current_user=current_user,
        bouquetId=bouquetId,
        document_id=payload.documentIds,
    )


@router.post("/getApprovedDocs")
def get_approved_documents(
    filters: DocFilter,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return get_approved_documents_service(
        db=db, current_user=current_user, filters=filters
    )
