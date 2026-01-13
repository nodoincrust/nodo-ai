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
from app.AIhelpers.chunk_helper import chunkText
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


def compute_display_status(document, steps):
    status = document.status

    # Draft before submission
    if status == "DRAFT":
        return "DRAFT"

    # Submitted but not yet approved by 1st assignee
    if status == "SUBMITTED":
        pending_step = next((s for s in steps if s.status == "PENDING"), None)
        if pending_step:
            return f"Pending on {normalize_role_name(pending_step.approver_type)}"
        return "Submitted"

    # Rejected case
    if status == "REJECTED":
        rejected_step = next((s for s in steps if s.status == "REJECTED"), None)
        if rejected_step:
            return f"Rejected by {normalize_role_name(rejected_step.approver_type)}"
        return "Rejected"

    # Under review — mid workflow
    if status in ("UNDER_REVIEW",):
        pending_step = next((s for s in steps if s.status == "PENDING"), None)
        if pending_step:
            return f"Pending on {normalize_role_name(pending_step.approver_type)}"
        return "Under Review"

    # Approved final
    if status == "APPROVED":
        return "Approved"

    # Fallback
    return status


# =======================================================
# Document AI Processing
# =======================================================


def processDocument(
    *,
    filePath: str,
    documentId: int,
    filename: str,
    fileType: str,
    fileSizeMb: float,
) -> Dict:

    if not isinstance(documentId, int):
        raise TypeError(
            f"documentId must be int, got {type(documentId)} → {documentId}"
        )

    db: Session = SessionLocal()
    chunksCreated = 0
    ocrUsed = False

    try:
        session = ChatSession()
        db.add(session)
        db.flush()

        aiDocument = (
            db.query(AIDocument).filter(AIDocument.document_id == documentId).first()
        )

        if not aiDocument:
            aiDocument = AIDocument(
                document_id=documentId,
                session_id=session.session_id,
                filename=filename,
                file_type=fileType,
                file_size_mb=fileSizeMb,
            )
            db.add(aiDocument)
            db.commit()
        else:
            session.session_id = aiDocument.session_id

        for pageNumber, rawText, usedOcr in iterateFilePages(filePath):
            if not rawText or not rawText.strip():
                continue

            ocrUsed |= usedOcr

            for chunk in chunkText(rawText):
                db.add(
                    DocumentChunk(
                        id=uuid.uuid4(),
                        ai_document_id=aiDocument.id,
                        session_id=aiDocument.session_id,
                        chunk_index=chunksCreated,
                        chunk_text=chunk,
                        page_number=pageNumber,
                    )
                )
                chunksCreated += 1

        db.commit()

        return {
            "chunks": chunksCreated,
            "ocr_used": ocrUsed,
            "session_id": str(aiDocument.session_id),
        }

    except Exception:
        db.rollback()
        logger.exception("Document ingestion failed")
        raise

    finally:
        db.close()


# =======================================================
# Draft + Metadata Save
# =======================================================


def saveDocument(
    db: Session,
    *,
    documentId: int,
    payload: DocumentSaveSchema,
    currentUser: dict,
):
    document = (
        db.query(Document)
        .filter(
            Document.id == documentId,
            Document.is_delete.is_(False),
        )
        .first()
    )

    if not document:
        raise HTTPException(404, "Document not found")

    if document.uploaded_by != currentUser["user_id"]:
        raise HTTPException(403, "Permission denied")

    if document.status != "DRAFT":
        raise HTTPException(400, "Only draft documents can be edited")

    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    if not version:
        raise HTTPException(500, "Document version not found")

    if payload.summary is not None:
        version.summary = payload.summary

    if payload.tags is not None:
        version.tags = payload.tags

    ai_document = (
        db.query(AIDocument).filter(AIDocument.document_id == document.id).first()
    )

    if not ai_document:
        raise HTTPException(500, "AI document not found")

    if payload.summary is not None:
        ai_document.summary = DocumentSummary(
            summary_text=payload.summary,
            tags=payload.tags or [],
            citations=[],
        )

    review = DocumentReview(
        document_id=document.id,
        reviewed_by=None,
        status="PENDING",
    )

    db.add(review)
    db.commit()

    return {
        "documentId": document.id,
        "status": document.status,
        "summary": version.summary,
        "tags": version.tags,
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

    document = Document(
        company_id=company.id,
        department_id=departmentId,
        uploaded_by=currentUser["user_id"],
        status="DRAFT",
    )
    db.add(document)
    db.flush()

    docDir = os.path.join(
        BASE_STORAGE_PATH,
        "companies",
        str(company.id),
        "documents",
        str(document.id),
    )
    os.makedirs(docDir, exist_ok=True)

    permanentPath = os.path.join(docDir, f"v1_{originalFilename}")
    shutil.move(tempFilePath, permanentPath)

    sizeBytes = os.path.getsize(permanentPath)

    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        file_path=permanentPath,
        file_name=originalFilename,
        file_size_bytes=sizeBytes,
        created_by=currentUser["user_id"],
    )

    db.add(version)
    company.remaining_space -= sizeBytes
    db.commit()

    return {"document_id": document.id, "file_path": permanentPath}


# =======================================================
# FULL DETAILS + Visibility + Workflow
# =======================================================


def get_document_full_details(
    db: Session,
    *,
    document_id: int,
    current_user: dict,
):

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
        raise HTTPException(status_code=404, detail="Document not found")

    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    steps = (
        db.query(DocumentApprovalStep)
        .filter(
            DocumentApprovalStep.document_id == document.id,
            DocumentApprovalStep.version_id == version.id,
        )
        .order_by(DocumentApprovalStep.step_order)
        .all()
    )
    
    viewer_id = current_user["user_id"]
    viewer_step = next((s for s in steps if s.assigned_to == viewer_id), None)

    # if viewer_step:
    #  is_actionable = (viewer_step.status == "PENDING")
    # else:
    #  is_actionable = False
    
    if viewer_step:
     is_actionable = (viewer_step.status != "PENDING")
    else:
     is_actionable = True




    pending_step = next((s for s in steps if s.status == "PENDING"), None)
    pending_on = pending_step.approver_type.title() if pending_step else None
    
    rejected_step = next((s for s in steps if s.status == "REJECTED"), None)
    remarks = rejected_step.remarks if rejected_step else None


    display_status = compute_display_status(document, steps)

    ai_document = (
        db.query(AIDocument).filter(AIDocument.document_id == document.id).first()
    )

    summary = ai_document.summary if ai_document else None

    review = (
        db.query(DocumentReview)
        .filter(DocumentReview.document_id == document.id)
        .order_by(DocumentReview.created_at.desc())
        .first()
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
            "is_actionable":is_actionable,
            "remark":remarks
        },
        "file": {
            "file_name": version.file_name if version else None,
            "file_path": (
                "/" + version.file_path.replace("\\", "/").lstrip("/")
                if version
                else None
            ),
            "file_size_bytes": version.file_size_bytes if version else None,
            "version_number": version.version_number if version else None,
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
            "text": summary.summary_text if summary else None,
            "tags": summary.tags or [] if summary else [],
            "citations": summary.citations or [] if summary else [],
        },
        "review": {
            "status": review.status if review else None,
            "reviewed_by": review.reviewed_by if review else None,
        },
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

    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

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

    workflow = (
        db.query(DocumentWorkflowRun)
        .filter(
            DocumentWorkflowRun.document_id == document.id,
            DocumentWorkflowRun.version_id == version.id,
        )
        .first()
    )

    workflow.workflow_status = "COMPLETED"
    workflow.public_at = datetime.utcnow()

    version.visibility = "COMPANY"
    version.public_at = datetime.utcnow()

    document.status = "APPROVED"
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

    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

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
    document.status = "UNDER_REVIEW"
    document.current_version = new_version.version_number
    document.current_assignee_id = old_steps[1].assigned_to
    document.current_step_order = 2

    db.commit()

    return {"message": "Document reuploaded and workflow restarted."}


def get_approver_inbox(
    db: Session,
    current_user: dict,
    search: str | None,
    page: int,
    size: int,
):
    user_id = current_user["user_id"]
    offset = (page - 1) * size

    query = (
        db.query(DocumentApprovalStep, Document, DocumentVersion, User)
        .join(Document, Document.id == DocumentApprovalStep.document_id)
        .join(
            DocumentVersion,
            (DocumentVersion.document_id == Document.id)
            & (DocumentVersion.version_number == Document.current_version),
            isouter=True,
        )
        .join(User, User.id == Document.uploaded_by)
        .filter(
            DocumentApprovalStep.assigned_to == user_id,
            DocumentApprovalStep.status.in_(["PENDING", "APPROVED", "REJECTED"]),
            Document.status.in_(["SUBMITTED", "APPROVED", "REJECTED"]),
            Document.is_delete.is_(False),
        )
    )

    if search:

        search_pattern = f"%{search.lower()}%"
        query = query.filter(func.lower(DocumentVersion.file_name).like(search_pattern))

    total = query.count()

    rows = query.order_by(Document.created_at.desc()).offset(offset).limit(size).all()

    data = []
    for i, (my_step, doc, version, uploader) in enumerate(rows, start=1):

        # fetch real version again in case join failed
        latest_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.desc())
            .first()
        )

        # fetch steps
        steps = (
            db.query(DocumentApprovalStep)
            .filter(
                DocumentApprovalStep.version_id
                == (latest_version.id if latest_version else None)
            )
            .order_by(DocumentApprovalStep.step_order)
            .all()
        )
        viewer_status = my_step.status 
        is_actionable = (my_step.status == "PENDING")

        data.append(
            {
                "document_id": doc.id,
                "file_name": latest_version.file_name if latest_version else None,
                "version_number": (
                    latest_version.version_number if latest_version else None
                ),
                "status": viewer_status,
                "is_actionable": is_actionable,
                "is_approved": True if viewer_status == "APPROVED" else False,
                "uploaded_by": {"user_id": uploader.id, "name": uploader.name},
                "submitted_at": doc.created_at,
            }
        )

    return {
        "statusCode": 200,
        "message": "Inbox fetched successfully",
        "page": page,
        "size": size,
        "total": total,
        "data": data,
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

            else:  # WAITING state
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
