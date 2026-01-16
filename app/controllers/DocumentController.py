import shutil
import os
import tempfile
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
    Depends,
    Query,
    Body
)
from sqlalchemy.orm import Session
from app.helpers import get_current_user, get_db
from app.schemas import (
    DocumentSaveSchema,
    CreateEmployeeSchema,
    GetApprovalDocumentList,
    UpdateEmployeeSchema,
)
from app.services.BouquetService import appendDocumentToBouquet, createBouquet, deleteBouquet, getAllBouquets, removeDocumentFromBouquet
from app.services.document_service import (
    approve_document_step,
    get_approver_inbox,
    processDocument,
    createDocumentDraft,
    reject_document_step,
    reupload_document_version,
    saveDocument,
    get_document_full_details,
)
from app.services.Companyservice import (
    add_employee_service,
    update_employee_service,
    delete_employee_details,
    get_employee_list,
)
from app.services.ai_DBservice import getOrCreateSessionForDocument

router = APIRouter(prefix="/nodo/newdocuments")

ASYNC_THRESHOLD_MB = 2.0


# ────────────────────────────────────────────────
# Document Endpoints
# ────────────────────────────────────────────────

@router.post("/upload")
async def uploadDocument(
    backgroundTasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    print("Current user:", current_user)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    _, extension = os.path.splitext(file.filename)
    with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tempPath = tmp.name

    if not os.path.exists(tempPath) or os.path.getsize(tempPath) == 0:
        os.remove(tempPath)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    fileSizeMb = os.path.getsize(tempPath) / (1024 * 1024)

    try:
        # createDocumentDraft moves file → returns permanent path info
        draft_result = createDocumentDraft(
            db=db,
            tempFilePath=tempPath,
            originalFilename=file.filename,
            departmentId=current_user.get("department_id"),
            currentUser=current_user,
        )

        # Extract important values from result
        document_id = draft_result["document_id"]
        permanent_file_path = draft_result["file_path"]  # ← This is the new location!

        # Use permanent path for processing (file is now here!)
        if fileSizeMb >= ASYNC_THRESHOLD_MB:
            backgroundTasks.add_task(
                processDocument,
                filePath=permanent_file_path,   # ← Fixed: use permanent path
                document_id=document_id,
                filename=file.filename,
                fileType=file.content_type,
                fileSizeMb=fileSizeMb,
            )
            return {
                "status": "processing",
                "documentId": document_id,
                "fileSizeMb": round(fileSizeMb, 2),
            }

        result = processDocument(
            filePath=permanent_file_path, 
            document_id=document_id,
            filename=file.filename,
            fileType=file.content_type,
            fileSizeMb=fileSizeMb,
        )

        sessionId = getOrCreateSessionForDocument(document_id)

        return {
            "status": "success",
            "documentId": document_id,
            "sessionId": sessionId,
            "chunks": result.get("chunks", 0),
            "ocrUsed": result.get("ocr_used", False),
            "fileSizeMb": round(fileSizeMb, 2),
        }

    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        # Only try to clean temp file if it still exists (in case draft failed)
        if os.path.exists(tempPath):
            try:
                os.remove(tempPath)
            except Exception:
                pass

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
    payload: dict = Body(...),
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
    payload: GetApprovalDocumentList,  # Assuming this schema exists
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    print("Payload:", payload.dict())
    return get_approver_inbox(
        db=db,
        current_user=current_user,
        search=payload.search,
        status=payload.status,
        page=payload.page,
        pagelimit=payload.pagelimit
    )


# ────────────────────────────────────────────────
# Bouquet Endpoints
# ────────────────────────────────────────────────

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
def getAllBoq(
    db: Session = Depends(get_db),
    currentUser: dict = Depends(get_current_user),
):
    result = getAllBouquets(db=db, currentUserId=currentUser["user_id"])  # Assuming function name
    if not result:
        raise HTTPException(404, "Bouquet not found")
    return result


# @router.get("/bouquets/{bouquetId}")
# def getBouquet(
#     bouquetId: int,
#     db: Session = Depends(get_db),
# ):
#     result = getBouquetById(db, bouquetId)
#     if not result:
#         raise HTTPException(404, "Bouquet not found")
#     return result


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
