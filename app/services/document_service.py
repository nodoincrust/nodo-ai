import uuid
import logging
import shutil
import os
from typing import Dict
from sqlalchemy.orm import Session
from app.helpers import normalize_role
from sqlalchemy import func
from fastapi import HTTPException
from datetime import datetime
from app.db import SessionLocal
from app.models import (
    Document,
    AIDocument,
    ChatSession,
    DocumentChunk,
    DocumentVersion,
    DocumentReview,
    Company,
    DocumentSummary,
    DocumentApprovalStep,
    DocumentWorkflowRun,
    User,
)
from app.AIhelpers.chunk_helper import chunkText, createDocumentChunks
from app.AIhelpers.format_helper import iterateFilePages
from app.schemas import DocumentSaveSchema

BASE_STORAGE_PATH = "storage"
logger = logging.getLogger(__name__)
MAX_UPLOAD_MB = 50
CHUNK_BATCH_SIZE = 32

# =======================================================
# Helper functions for workflow display
# =======================================================
def normalize_role_name(r: str):
    if r == "EMPLOYEE":
        return "Uploader"
    if r == "DEPARTMENT_HEAD":
        return "Department Head"
    if r == "COMPANY_ADMIN":
        return "Company Admin"
    return r.title()

def compute_display_status(workflow, steps):
    # If workflow missing (legacy or bad data)
    if not workflow:
        return "DRAFT"
    wf_status = workflow.workflow_status # version-level lifecycle
    # === REJECTED VERSION ===
    if wf_status == "REJECTED":
        rejected_step = next((s for s in steps if s.status == "REJECTED"), None)
        if rejected_step:
            return f"Rejected by {normalize_role_name(rejected_step.approver_type)}"
        return "Rejected"
    # === APPROVED VERSION ===
    if wf_status == "COMPLETED":
        return "Approved"
    # === IN PROGRESS → Display pending chain ===
    pending_step = next((s for s in steps if s.status == "PENDING"), None)
    if pending_step:
        return f"Pending on {normalize_role_name(pending_step.approver_type)}"
    return "Pending"

# =======================================================
# Document AI Processing
# =======================================================
def processDocument(
    *,
    document_id: int,
    filename: str,
    fileType: str,
    fileSizeMb: float,
    filePath: str = None,
    versionId: int = None,  # Optional for legacy
) -> Dict:
    if not isinstance(document_id, int):
        raise TypeError(f"document_id must be int, got {type(document_id)} → {document_id}")

    db: Session = SessionLocal()
    ocrUsed = False
    chunksCreated = 0

    try:
        # 1. Get the correct version
        if versionId:
            version = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == document_id,
                DocumentVersion.id == versionId
            ).first()
        else:
            version = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == document_id
            ).order_by(DocumentVersion.version_number.desc()).first()

        if not version or not version.file_path:
            raise FileNotFoundError(f"No valid version/file found for document_id={document_id}")

        storedFilePath = filePath or version.file_path

        if not os.path.exists(storedFilePath):
            raise FileNotFoundError(f"Stored document file missing: {storedFilePath}")

        # 2. Get or create AIDocument
        aiDocument = db.query(AIDocument).filter(
            AIDocument.document_id == document_id,
            AIDocument.version_id == version.id
        ).first()

        if not aiDocument:
            session = ChatSession()
            db.add(session)
            db.flush()

            aiDocument = AIDocument(
                document_id=document_id,
                version_id=version.id,
                session_id=session.session_id,
                filename=filename,
                file_type=fileType,
                file_size_mb=fileSizeMb,
            )
            db.add(aiDocument)
            db.commit()

        # 3. IMPORTANT FIX: Check if chunks already exist for this AI document
        existing_count = db.query(func.count(DocumentChunk.id)).filter(
            DocumentChunk.ai_document_id == aiDocument.id
        ).scalar()

        if existing_count > 0:
            logger.info(f"Skipping chunking: {existing_count} chunks already exist for ai_document_id={aiDocument.id}")
            return {
                "status": "already_processed",
                "document_id": document_id,
                "chunks": existing_count,
                "ocr_used": False,
                "message": "Document already processed — skipping chunk creation"
            }

        # 4. Only chunk if nothing exists
        lastChunkIndex = 0
        for pageNumber, rawText, usedOcr in iterateFilePages(storedFilePath):
            if not rawText or not rawText.strip():
                continue

            ocrUsed |= usedOcr

            created = createDocumentChunks(
                db=db,
                ai_document_id=aiDocument.id,
                session_id=aiDocument.session_id,
                pages=[(pageNumber, rawText)],
                start_index=lastChunkIndex,
            )

            lastChunkIndex += created
            chunksCreated += created

        db.commit()

        return {
            "status": "success",
            "document_id": document_id,
            "chunks": chunksCreated,
            "ocr_used": ocrUsed,
            "file_size_mb": round(fileSizeMb, 2),
        }

    except Exception as e:
        db.rollback()
        logger.exception("Document ingestion failed")
        raise

    finally:
        db.close()

def saveDocument(
    db: Session,
    *,
    documentId: int,
    payload: DocumentSaveSchema,
    currentUser: dict,
):
    document = (
        db.query(Document)
        .filter(Document.id == documentId, Document.is_delete.is_(False))
        .first()
    )
    if not document:
        raise HTTPException(404, "Document not found")
    if document.uploaded_by != currentUser["user_id"]:
        raise HTTPException(403, "Permission denied")
    if document.status not in ("DRAFT", "REUPLOADED"):
        raise HTTPException(400, "Only draft documents can be edited")
    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )
    if not version:
        raise HTTPException(500, "No version found")
    if payload.summary is not None:
        version.summary = payload.summary
    if payload.tags is not None:
        version.tags = payload.tags
    ai_document = (
        db.query(AIDocument).filter(AIDocument.document_id == document.id).first()
    )
    if not ai_document:
        raise HTTPException(500, "AI Document missing")
    version_summary = (
        db.query(DocumentSummary)
        .filter(
            DocumentSummary.ai_document_id == ai_document.id,
            DocumentSummary.version_id == version.id,
        )
        .first()
    )
    if version_summary:
        version_summary.summary_text = payload.summary or version_summary.summary_text
        version_summary.tags = payload.tags or version_summary.tags or []
        version_summary.citations = []
    else:
        version_summary = DocumentSummary(
            ai_document_id=ai_document.id,
            version_id=version.id,
            summary_text=payload.summary or "",
            tags=payload.tags or [],
            citations=[],
        )
        db.add(version_summary)

    # From first: add review
    review = DocumentReview(
        document_id=document.id,
        reviewed_by=None,
        status="PENDING",
    )
    db.add(review)

    db.commit()
    return {
        "document_id": document.id,
        "version_id": version.id,
        "version_number": version.version_number,
        "summary": version_summary.summary_text,
        "tags": version_summary.tags,
        "status": document.status,
    }

# =======================================================
# Draft Create
# =======================================================
def createDocumentDraft(
    db: Session,
    *,
    tempFilePath: str,
    originalFilename: str,
    departmentId: int,
    currentUser: dict,
):
    company = (
        db.query(Company)
        .filter(
            Company.id == currentUser["company_id"],
            Company.is_delete.is_(False),
        )
        .first()
    )
    if not company:
        raise HTTPException(404, "Company not found")
    # Create base document
    document = Document(
        company_id=company.id,
        department_id=departmentId,
        uploaded_by=currentUser["user_id"],
        status="DRAFT",
    )
    db.add(document)
    db.flush()
    # Ensure storage path
    docDir = os.path.join(
        BASE_STORAGE_PATH,
        "companies",
        str(company.id),
        "documents",
        str(document.id),
    )
    os.makedirs(docDir, exist_ok=True)
    # Move uploaded file into permanent storage
    permanentPath = os.path.join(docDir, f"v1_{originalFilename}")
    shutil.move(tempFilePath, permanentPath)
    sizeBytes = os.path.getsize(permanentPath)
    # Create Version 1
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        file_path=permanentPath,
        file_name=originalFilename,
        file_size_bytes=sizeBytes,
        created_by=currentUser["user_id"],
        summary=None, # per version summary support
        tags=[], # per version tags support
    )
    db.add(version)
    # Update document pointer
    document.current_version = 1
    # Reduce company space
    company.remaining_space -= sizeBytes
    db.commit()
    # In document_service.py → createDocumentDraft
    return {
        "document_id": document.id,
        "version_id": version.id,
        "version_number": 1,
        "file_path": permanentPath, 
    }

# =======================================================
# FULL DETAILS + Visibility + Workflow
# =======================================================
def get_document_full_details(
    db: Session,
    *,
    document_id: int,
    version: int | None = None,
    current_user: dict,
):
    # 1. Select version
    if version is not None:
        version_obj = (
            db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_number == version,
            )
            .first()
        )
        if not version_obj:
            raise HTTPException(404, "Version not found")
    else:
        version_obj = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
            .first()
        )
    # 2. Validate document
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.is_delete.is_(False),
            Document.company_id == current_user["company_id"],
        )
        .first()
    )
    if not document:
        raise HTTPException(404, "Document not found")
    # 3. Fetch approval steps for same version
    steps = (
        db.query(DocumentApprovalStep)
        .filter(
            DocumentApprovalStep.document_id == document.id,
            DocumentApprovalStep.version_id == version_obj.id,
        )
        .order_by(DocumentApprovalStep.step_order)
        .all()
    )
    # 4. Viewer context (actionable for approver)
    viewer_id = current_user["user_id"]
    viewer_step = next((s for s in steps if s.assigned_to == viewer_id), None)
    is_actionable = viewer_step and viewer_step.status == "PENDING"
    # 5. Rejected remarks (if any)
    rejected_step = next((s for s in steps if s.status == "REJECTED"), None)
    remarks = rejected_step.remarks if rejected_step else None
    workflow = (
        db.query(DocumentWorkflowRun)
        .filter(
            DocumentWorkflowRun.document_id == document.id,
            DocumentWorkflowRun.version_id == version_obj.id,
        )
        .first()
    )
    display_status = compute_display_status(workflow, steps)
    # 7. Fetch AI summary per version
    ai_document = (
        db.query(AIDocument)
        .filter(
            AIDocument.document_id == document.id,
            AIDocument.version_id == version_obj.id,
        )
        .first()
    )
    # fallback for legacy docs
    if not ai_document:
        ai_document = (
            db.query(AIDocument)
            .filter(
                AIDocument.document_id == document.id,
                AIDocument.version_id.is_(None),
            )
            .first()
        )
    summary_entry = (
        db.query(DocumentSummary)
        .join(AIDocument, AIDocument.id == DocumentSummary.ai_document_id)
        .filter(
            AIDocument.document_id == document.id,
            DocumentSummary.version_id == version_obj.id,
        )
        .first()
    )
    # 8. Latest review (optional)
    review = (
        db.query(DocumentReview)
        .filter(DocumentReview.document_id == document.id)
        .order_by(DocumentReview.created_at.desc())
        .first()
    )
    versions = (
        db.query(DocumentVersion.version_number, DocumentVersion.created_at)
        .filter(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.asc())
        .all()
    )
    return {
        "document": {
            "id": document.id,
            "status": document.status,
            "display_status": display_status,
            "is_approved": (document.status == "APPROVED"),
            "is_active": document.is_active,
            "created_at": document.created_at,
            "current_version": document.current_version,
            "uploaded_by": document.uploaded_by,
            "department_id": document.department_id,
            "company_id": document.company_id,
            "is_actionable": is_actionable,
            "remark": remarks,
        },
        "file": {
            "file_name": version_obj.file_name,
            "file_path": "/" + version_obj.file_path.replace("\\", "/").lstrip("/"),
            "file_size_bytes": version_obj.file_size_bytes,
            "version_number": version_obj.version_number,
        },
        "ai": {
            "ai_document_id": ai_document.id if ai_document else None,
            "session_id": str(ai_document.session_id) if ai_document else None,
            "file_type": ai_document.file_type if ai_document else None,
            "file_size_mb": (
                float(ai_document.file_size_mb)
                if ai_document and ai_document.file_size_mb
                else None
            ),
        },
        "summary": {
            "text": summary_entry.summary_text if summary_entry else None,
            "tags": summary_entry.tags or [] if summary_entry else [],
            "citations": summary_entry.citations or [] if summary_entry else [],
        },
        "versions": [{"version": v[0], "created_at": v[1]} for v in versions],
    }

# =======================================================
# Approve Step
# =======================================================
def approve_document_step(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    current_user: dict,
):
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.is_delete == False)
        .first()
    )
    if not document:
        raise HTTPException(404, "Document not found")
    # latest version
    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )
    if not version:
        raise HTTPException(500, "Version not found")
    current_step = (
        db.query(DocumentApprovalStep)
        .filter(
            DocumentApprovalStep.document_id == document_id,
            DocumentApprovalStep.version_id == version.id,
            DocumentApprovalStep.status == "PENDING",
        )
        .first()
    )
    if not current_step:
        raise HTTPException(400, "No pending approval step")
    if current_step.assigned_to != user_id:
        raise HTTPException(403, "Not authorized to approve this step")
    current_step.status = "APPROVED"
    current_step.action_at = datetime.utcnow()
    next_step = (
        db.query(DocumentApprovalStep)
        .filter(
            DocumentApprovalStep.document_id == document.id,
            DocumentApprovalStep.version_id == version.id,
            DocumentApprovalStep.step_order == current_step.step_order + 1,
        )
        .first()
    )
    if next_step:
        next_step.status = "PENDING"
        document.current_assignee_id = next_step.assigned_to
        document.current_step_order = next_step.step_order
        db.commit()
        return {"message": "Approved. Moving to next approver."}
    # Final approval
    workflow = (
        db.query(DocumentWorkflowRun)
        .filter(
            DocumentWorkflowRun.document_id == document.id,
            DocumentWorkflowRun.version_id == version.id,
        )
        .first()
    )
    if workflow:
        workflow.workflow_status = "COMPLETED"
        workflow.public_at = datetime.utcnow()
    version.visibility = "COMPANY"
    version.public_at = datetime.utcnow()
    document.status = "APPROVED"
    document.current_version = version.version_number # <--- Important
    document.current_assignee_id = None
    document.current_step_order = None
    db.commit()
    return {"message": "Final approval completed. Document is now public."}

# =======================================================
# Reject Step
# =======================================================
def reject_document_step(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    remarks: str | None = None,
):
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.is_delete == False)
        .first()
    )
    if not document:
        raise HTTPException(404, "Document not found")
    # latest version
    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )
    if not version:
        raise HTTPException(500, "Version not found")
    current_step = (
        db.query(DocumentApprovalStep)
        .filter(
            DocumentApprovalStep.document_id == document_id,
            DocumentApprovalStep.version_id == version.id,
            DocumentApprovalStep.status == "PENDING",
        )
        .first()
    )
    if not current_step:
        raise HTTPException(400, "No pending step to reject")
    if current_step.assigned_to != user_id:
        raise HTTPException(403, "Not authorized to reject")
    current_step.status = "REJECTED"
    current_step.action_at = datetime.utcnow()
    current_step.remarks = remarks or None
    print("remarks service-----",remarks)
    workflow = (
        db.query(DocumentWorkflowRun)
        .filter(
            DocumentWorkflowRun.document_id == document_id,
            DocumentWorkflowRun.version_id == version.id,
        )
        .first()
    )
    workflow.workflow_status = "REJECTED"
    workflow.rejected_by = user_id
    workflow.rejected_at = datetime.utcnow()
    # optional per-version reject info
    version.rejected_remarks = remarks
    version.rejected_at = datetime.utcnow()
    version.status = "REJECTED"
    document.status = "REJECTED"
    document.current_assignee_id = document.uploaded_by
    document.current_step_order = None
    db.commit()
    return {"message": "Document rejected and returned to uploader."}

# =======================================================
# Reupload Version
# =======================================================
def reupload_document_version(
    db: Session,
    *,
    document_id: int,
    file_path: str,
    file_name: str,
    created_by: int,
):
    last_version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    new_version_number = last_version.version_number + 1

    new_version = DocumentVersion(
        document_id=document_id,
        version_number=new_version_number,
        file_path=file_path,
        file_name=file_name,
        file_size_bytes=os.path.getsize(file_path),
        created_by=created_by,
    )
    db.add(new_version)
    db.flush()

    old_steps = (
        db.query(DocumentApprovalStep)
        .filter(
            DocumentApprovalStep.document_id == document_id,
            DocumentApprovalStep.version_id == last_version.id,
        )
        .order_by(DocumentApprovalStep.step_order)
        .all()
    )

    step_order = 1

    uploader_step = old_steps[0]
    db.add(
        DocumentApprovalStep(
            document_id=document_id,
            version_id=new_version.id,
            step_order=step_order,
            assigned_to=uploader_step.assigned_to,
            approver_type=uploader_step.approver_type,
            status="APPROVED",
            action_at=datetime.utcnow(),
        )
    )
    step_order += 1

    for idx, step in enumerate(old_steps[1:], start=1):
        db.add(
            DocumentApprovalStep(
                document_id=document_id,
                version_id=new_version.id,
                step_order=step_order,
                assigned_to=step.assigned_to,
                approver_type=step.approver_type,
                status="PENDING" if idx == 1 else "WAITING",
            )
        )
        step_order += 1

    workflow = DocumentWorkflowRun(
        document_id=document_id,
        version_id=new_version.id,
        workflow_status="IN_PROGRESS",
    )
    db.add(workflow)

    document = db.query(Document).filter(Document.id == document_id).first()
    # if no hierarchy beyond uploader => auto approve whole document
    if len(old_steps) == 1:
        document.status = "APPROVED"
        document.current_version = new_version_number
        document.current_assignee_id = None
        document.current_step_order = None
        db.commit()
        return {
            "message": "Document reuploaded and auto-approved.",
            "version": new_version_number,
            "file_path": file_path,
            "version_id": new_version.id,
        }
    # otherwise pending on next approver
    document.status = "REUPLOADED" # matches SUBMITTED flow
    document.current_version = new_version_number
    document.current_step_order = 2
    document.current_assignee_id = old_steps[1].assigned_to
    db.commit()
    return {
        "message": "Document reuploaded and workflow restarted.",
        "version": new_version_number,
        "file_path": file_path,
        "version_id": new_version.id,
    }

def get_approver_inbox(
    db: Session,
    current_user: dict,
    search: str | None,
    status: str | None,
    page: int,
    pagelimit: int,
):
    user_id = current_user["user_id"]
    offset = (page - 1) * pagelimit
    # Base docs for this company (no status filter)
    docs = (
        db.query(Document, User)
        .join(User, User.id == Document.uploaded_by)
        .filter(Document.is_delete.is_(False))
        .order_by(Document.created_at.desc())
        .all()
    )
    data = []
    for doc, uploader in docs:
        # ---- v1 for file name ----
        first_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.asc())
            .first()
        )
        if not first_version:
            continue
        # ---- latest version for approval chain ----
        latest_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.desc())
            .first()
        )
        if not latest_version:
            continue
        # ---- find user's step in latest ----
        my_step = (
            db.query(DocumentApprovalStep)
            .filter(
                DocumentApprovalStep.document_id == doc.id,
                DocumentApprovalStep.version_id == latest_version.id,
                DocumentApprovalStep.assigned_to == user_id,
            )
            .first()
        )
        if not my_step:
            continue # user not part of workflow
        viewer_status = my_step.status # exact: PENDING/APPROVED/REJECTED
        is_actionable = (my_step.status == "PENDING")
        # ---- search filtering ----
        if search:
            search_pattern = f"%{search.lower()}%"
            if not func.lower(first_version.file_name).like(search_pattern):
                continue
        # ---- status filter ----
        if status:
            if viewer_status != status.upper():
                continue
        data.append(
            {
                "document_id": doc.id,
                "file_name": first_version.file_name,
                "version_number": latest_version.version_number,
                "status": viewer_status, # EXACT
                "is_actionable": is_actionable,
                "is_approved": True if viewer_status == "APPROVED" else False,
                "uploaded_by": {"user_id": uploader.id, "name": uploader.name},
                "submitted_at": doc.created_at,
            }
        )
    total = len(data)
    # --- pagination after filtering ---
    paginated = data[offset : offset + pagelimit]
    return {
        "statusCode": 200,
        "message": "Inbox fetched successfully",
        "page": page,
        "pagelimit": pagelimit,
        "total": total,
        "data": paginated,
    }

def compute_workflow_view(steps, viewer_id):
    """
    Converts raw approval steps into contextual display for given viewer.
    """
    view = []
    pending_step = next((s for s in steps if s.status == "PENDING"), None)
    for s in steps:
        if s.assigned_to == viewer_id:
            # Current user is the owner of this step
            if s.status == "PENDING":
                display = "Pending"
                actionable = True
            elif s.status == "APPROVED":
                display = "Approved"
                actionable = False
            elif s.status == "REJECTED":
                display = "Rejected "
                actionable = False
            else:
                display = s.status.title()
                actionable = False
        else:
            # Viewer is not the owner of this step
            if s.status == "APPROVED":
                display = f"Approved by {normalize_role_name(s.approver_type)}"
                actionable = False
            elif s.status == "REJECTED":
                display = f"Rejected by {normalize_role_name(s.approver_type)}"
                actionable = False
            elif s.status == "PENDING":
                display = f"Pending on {normalize_role_name(s.approver_type)}"
                actionable = False
            else: # WAITING state
                display = "Waiting"
                actionable = False
        view.append(
            {
                "step_order": s.step_order,
                "role": normalize_role_name(s.approver_type),
                "assigned_to": s.assigned_to,
                "status": s.status,
                "display": display,
                "actionable": actionable,
            }
        )
    return view