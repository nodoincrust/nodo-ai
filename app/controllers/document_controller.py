import json
from typing import List
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Depends,
    HTTPException,
    BackgroundTasks,
    Form,
    Request,
    Body,
    
)
import uuid
from fastapi.responses import FileResponse
from pdf2docx import Converter
from sqlalchemy.orm import Session
import shutil
import tempfile
import os
from app.models import Document
from app.helpers import get_db, get_current_user
from app.permissions import require_company_scope, require_menu_permission
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
    ShareRequest,
    SharedDocViewRequest,
    FormTemplateCreate,
    getTemplate,
    templateResponse
    
)
from app.services.document_service import (
    details_editor,
    processDocument,
    createDocumentDraft,
    saveDocument,
    get_document_full_details,
    approve_document_step,
    reject_document_step,
    reupload_document_version,
    get_approver_inbox,
    processDocumentAndCleanup,
    verify_onlyoffice_callback,
    save_edited_document,
    
    
)
from app.services.bouquet_service import (
    createBouquet,
    getBouquetById,
    removeDocumentFromBouquet,
    deleteBouquet,
    getAllBoqList,
    get_approved_documents_service,
    update_boq_details,
    append_documents_to_bouquet,
    get_bouquet_documents_service,
    createTemplate,
    get_templates_list,
    get_templates_feilds,
    delete_templates_service,
    submit_template_form,
    fetch_template_submissions,
    get_template_response_user
    
)
from app.services.document_sharing import  share_docboq_service,list_shared_bouquets,list_shared_documents,list_shared_templates

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
    require_company_scope(currentUser)
    require_menu_permission(db, currentUser, "documents", "add")

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
                temp_path=tempPath,
                file_name=file.filename,
                created_by=currentUser["user_id"],
            )
 
            # AI Processing reads the temp file, then removes it
            background_tasks.add_task(
                processDocumentAndCleanup,
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
    s3Key = result["file_path"]
    versionId = result["version_id"]
 
    background_tasks.add_task(
        processDocumentAndCleanup,
        filePath=tempPath,
        documentId=businessDocumentId,
        versionId=versionId,
        filename=file.filename,
        fileType=file.content_type,
        fileSizeMb=fileSizeMb,
    )
 
    return {
        "status": "success",
        "documentId": businessDocumentId,
        "filepath": s3Key,
        "fileSizeMb": round(fileSizeMb, 2),
    }
 
 
@router.post("/{documentId}/save")
def saveDocumentApi(
    documentId: int,
    payload: DocumentSaveSchema,
    db: Session = Depends(get_db),
    currentUser=Depends(get_current_user),
):
    require_company_scope(currentUser)
    require_menu_permission(db, currentUser, "documents", "edit")
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "documents", "view")
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "documents", "edit")
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "documents", "edit")
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "documents", "add")
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
 
    try:
        result = reupload_document_version(
            db=db,
            document_id=document_id,
            temp_path=temp_path,
            file_name=file.filename,
            created_by=current_user["user_id"],
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
 
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "documents", "view")
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "bouquets", "add")
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "bouquets", "view")
    return getAllBoqList(db=db, current_user=current_user, filters=filters)


@router.post("/updateBouquet/{bouquetId}")
def update_bouquet(
    bouquetId: int,
    payload: updateBouquet,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "bouquets", "edit")
    return update_boq_details(
        db=db, current_user=current_user, payload=payload, bouquetId=bouquetId
    )


@router.get("/bouquets/{bouquetId}")
def getBouquet(
    bouquetId: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "bouquets", "view")
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "bouquets", "delete")
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "bouquets", "view")
    return get_bouquet_documents_service(
        db=db, current_user=current_user, bouquetId=bouquetId, filters=filters
    )


@router.post("/appendDocuments/{bouquetId}")
def append_documents(
    bouquetId: int,
    payload: AppendDocumentsSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "bouquets", "edit")
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
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "bouquets", "edit")
    return removeDocumentFromBouquet(
        db=db,
        current_user=current_user,
        bouquetId=bouquetId,
        document_id=payload.documentId,
    )


@router.post("/getApprovedDocs")
def get_approved_documents(
    filters: DocFilter,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "documents", "view")
    return get_approved_documents_service(
        db=db, current_user=current_user, filters=filters
    )



@router.post("/convert")
async def convert_pdf_to_docx(file: UploadFile = File(...)):

    # use python temp dir (works win/linux)
    tmp_dir = tempfile.gettempdir()

    input_path = os.path.join(tmp_dir, f"{uuid.uuid4()}.pdf")
    output_path = input_path.replace(".pdf", ".docx")

    # save uploaded file
    with open(input_path, "wb") as f:
        f.write(await file.read())

    # convert
    cv = Converter(input_path)
    cv.convert(output_path, start=0, end=None)
    cv.close()

    # return response
    return FileResponse(
        path=output_path,
        filename=file.filename.replace(".pdf", ".docx"),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@router.post("/share")
def share(payload:ShareRequest,
          db:Session=Depends(get_db),
          current_user=Depends(get_current_user)):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "shared_workspace", "add")
    return share_docboq_service(db,current_user,payload)

@router.post("/sharedDocument")
def get_shared_documents(
    payload: SharedDocViewRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "shared_workspace", "view")

    if payload.key not in ["doc", "boq", "template"]:
        raise HTTPException(
            status_code=400,
            detail="key must be in 'doc', 'boq' or 'template'"
        )

    if payload.order not in ["asc", "desc"]:
        raise HTTPException(
            status_code=400,
            detail="Order must be in 'asc' or 'desc'"
        )

    if payload.key == "doc":
        return list_shared_documents(db, current_user, payload)

    if payload.key == "boq":
        return list_shared_bouquets(
            db,
            current_user,
            payload.page,
            payload.pagelimit,
            payload.query,
            payload.sort,
            payload.order
        )

    if payload.key == "template":
        return list_shared_templates(db, current_user, payload)


@router.post("/savetemplate")
def save_template(
    payload:FormTemplateCreate,
    db:Session = Depends(get_db),
    current_user:dict = Depends(get_current_user)
    
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "templates", "add")
    print(payload)
    return createTemplate(db=db,payload=payload,current_user=current_user)


@router.post("/templates")
def get_templates(payload:getTemplate,db:Session = Depends(get_db), 
                  current_user:dict =Depends(get_current_user)                  
                  ):
                    require_company_scope(current_user)
                    require_menu_permission(db, current_user, "templates", "view")
                    return get_templates_list(db,current_user=current_user,payload=payload)
    

@router.post("/templatesFeilds/{template_id}")
def get_templatesFeilds(template_id:int,db:Session= Depends(get_db),current_user:dict=Depends(get_current_user)):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "templates", "view")
    return get_templates_feilds(db,current_user=current_user,template_id=template_id)

@router.delete("/deleteTemplate/{templateID}")
def delete_template(templateID:int,db:Session=Depends(get_db),current_user:dict=Depends(get_current_user)):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "templates", "delete")
    return delete_templates_service(db,current_user=current_user,templateID=templateID)


@router.post("/submitTemplateForm")
def submit_template(
    payload: str = Form(...),
    files: List[UploadFile] = File([]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "templates", "add")
    payload_dict = json.loads(payload)

    return submit_template_form(
        db=db,
        payload=payload_dict,
        files=files,
        current_user=current_user
    )

@router.get("/submissions/{template_id}")
def get_template_submission(template_id:int,db:Session=Depends(get_db),current_user:dict=Depends(get_current_user)):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "templates", "view")
    return fetch_template_submissions(
        db=db,
        template_id=template_id,
        current_user=current_user
    )
    
@router.post("/templates/submissions/filledvalues")
def get_template_response(
    payload: templateResponse,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "templates", "view")
    return get_template_response_user(
        db=db,
        template_id=payload.template_id,
        submitted_by=payload.submitted_by,
        current_user=current_user
    )

@router.post("/onlyoffice/callback/{document_id}")
async def onlyoffice_callback(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    body = await request.json()

    payload = verify_onlyoffice_callback(
        body, request.headers.get("Authorization")
    )

    status = payload.get("status")

    # 2 = editing session closed, 6 = force save during the session
    if status in (2, 6):
        download_url = payload.get("url")
        if not download_url:
            return {"error": 1}

        save_edited_document(
            db=db,
            document_id=document_id,
            download_url=download_url,
        )

    return {"error": 0}  # Acknowledge

