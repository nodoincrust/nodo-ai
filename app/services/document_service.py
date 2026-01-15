import uuid
import logging
import shutil
import os
from typing import Dict
from sqlalchemy.orm import Session
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
MAX_UPLOAD_MB = 50
CHUNK_BATCH_SIZE = 32



# Helper functions for workflow display
def normalize_role_name(r: str):
    if r == "EMPLOYEE":
        return "Uploader"
    if r == "DEPARTMENT_HEAD":
        return "Department Head"
    if r == "COMPANY_ADMIN":
        return "Company Admin"
    return r.title()


# def compute_display_status(document, steps):
#     status = document.status

#     # Draft before submission
#     if status == "DRAFT":
#         return "DRAFT"

#     # Submitted but not yet approved by 1st assignee
#     if status == "SUBMITTED":
#         pending_step = next((s for s in steps if s.status == "PENDING"), None)
#         if pending_step:
#             return f"Pending on {normalize_role_name(pending_step.approver_type)}"
#         return "Submitted"

#     # Rejected case
#     if status == "REJECTED":
#         rejected_step = next((s for s in steps if s.status == "REJECTED"), None)
#         if rejected_step:
#             return f"Rejected by {normalize_role_name(rejected_step.approver_type)}"
#         return "Rejected"

#     # Under review — mid workflow
#     if status in ("UNDER_REVIEW",):
#         pending_step = next((s for s in steps if s.status == "PENDING"), None)
#         if pending_step:
#             return f"Pending on {normalize_role_name(pending_step.approver_type)}"
#         return "Under Review1"

#     if status in ("REUPLOADED"):
#          pending_step = next((s for s in steps if s.status == "PENDING"), None)
#          print(pending_step)
#          if pending_step:
#             return f"Pending on {normalize_role_name(pending_step.approver_type)}"
#          return "Under Review2"

#     # Approved final
#     if status == "APPROVED":
#         return "Approved"

#     # Fallback
#     return status


def compute_display_status(workflow, steps):

    # If workflow missing 
    if not workflow:
        return "DRAFT"

    wf_status = workflow.workflow_status  # version-level lifecycle

    # REJECTED VERSION 
    if wf_status == "REJECTED":
        rejected_step = next((s for s in steps if s.status == "REJECTED"), None)
        if rejected_step:
            return f"Rejected by {normalize_role_name(rejected_step.approver_type)}"
        return "Rejected"

    # APPROVED VERSION 
    if wf_status == "COMPLETED":
        return "Approved"

    # IN PROGRESS → Display pending chain 
    pending_step = next((s for s in steps if s.status == "PENDING"), None)
    if pending_step:
        return f"Pending on {normalize_role_name(pending_step.approver_type)}"

    return "Pending"


# Document AI Processing
def processDocument(
    *,
    filePath: str,
    documentId: int,
    versionId: int,  # REQUIRED for version-based AI
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
        # Always create chat session per version
        session = ChatSession()
        db.add(session)
        db.flush()

        # ---------- AIDOCUMENT LOOKUP ----------
        aiDocument = (
            db.query(AIDocument)
            .filter(
                AIDocument.document_id == documentId,
                AIDocument.version_id == versionId,
            )
            .first()
        )

        # ---------- FALLBACK FOR LEGACY (versionId missing in old docs) ----------
        if not aiDocument and versionId == 1:
            aiDocument = (
                db.query(AIDocument)
                .filter(
                    AIDocument.document_id == documentId,
                    AIDocument.version_id.is_(None),
                )
                .first()
            )

            # Heal legacy: attach version id
            if aiDocument:
                aiDocument.version_id = versionId
                db.commit()

        # ---------- CREATE IF NOT FOUND ----------
        if not aiDocument:
            aiDocument = AIDocument(
                document_id=documentId,
                version_id=versionId,
                session_id=session.session_id,
                filename=filename,
                file_type=fileType,
                file_size_mb=fileSizeMb,
            )
            db.add(aiDocument)
            db.commit()
        else:
            # Reuse the same session
            session.session_id = aiDocument.session_id

        # ---------- CHUNKING & OCR ----------
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

# Draft + Metadata Save
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

    db.commit()

    return {
        "document_id": document.id,
        "version_id": version.id,
        "version_number": version.version_number,
        "summary": version_summary.summary_text,
        "tags": version_summary.tags,
        "status": document.status,
    }

# Draft Create
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
        summary=None,  # per version summary support
        tags=[],  # per version tags support
    )

    db.add(version)

    # Update document pointer
    document.current_version = 1

    # Reduce company space
    company.remaining_space -= sizeBytes

    db.commit()

    # Output includes version_id for AI
    return {
        "document_id": document.id,
        "version_id": version.id,
        "version_number": 1,
        "file_path": permanentPath,
    }

# FULL DETAILS + Visibility + Workflow

def get_document_full_details(
    db: Session,
    *,
    document_id: int,
    version: int | None = None,
    current_user: dict,
):
    # Select version
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

    # Validate document
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

    # Fetch approval steps for same version
    steps = (
        db.query(DocumentApprovalStep)
        .filter(
            DocumentApprovalStep.document_id == document.id,
            DocumentApprovalStep.version_id == version_obj.id,
        )
        .order_by(DocumentApprovalStep.step_order)
        .all()
    )

    # Viewer context (actionable for approver)
    viewer_id = current_user["user_id"]
    viewer_step = next((s for s in steps if s.assigned_to == viewer_id), None)
    is_actionable = viewer_step and viewer_step.status != "PENDING"

    # Rejected remarks (if any)
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

    # Compute final display status across workflow
    # display_status = compute_display_status(document, steps)

    # Fetch AI summary per version
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


# Approve Step

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
    document.current_version = version.version_number  # <--- Important
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

    # mark step rejected
    current_step.status = "REJECTED"
    current_step.action_at = datetime.utcnow()
    current_step.remarks = remarks or None

    # workflow state
    workflow = (
        db.query(DocumentWorkflowRun)
        .filter(
            DocumentWorkflowRun.document_id == document_id,
            DocumentWorkflowRun.version_id == version.id,
        )
        .first()
    )
    if workflow:
        workflow.workflow_status = "REJECTED"
        workflow.rejected_by = user_id
        workflow.rejected_at = datetime.utcnow()

    # optional per-version reject info
    version.rejected_remarks = remarks
    version.rejected_at = datetime.utcnow()
    version.status = "REJECTED"

    # document-level state
    document.status = "REJECTED"
    document.current_version = version.version_number  # <--- Important
    document.current_assignee_id = document.uploaded_by
    document.current_step_order = None

    db.commit()

    return {"message": "Document rejected and returned to uploader."}


# =======================================================
# Reupload Version
# =======================================================
# def reupload_document_version(
#     db: Session,
#     *,
#     document_id: int,
#     file_path: str,
#     file_name: str,
#     created_by: int,
# ):
#     # 1. Get last version
#     last_version = (
#         db.query(DocumentVersion)
#         .filter(DocumentVersion.document_id == document_id)
#         .order_by(DocumentVersion.version_number.desc())
#         .first()
#     )

#     new_version_number = last_version.version_number + 1

#     # 2. Create new version
#     new_version = DocumentVersion(
#         document_id=document_id,
#         version_number=new_version_number,
#         file_path=file_path,
#         file_name=file_name,
#         file_size_bytes=os.path.getsize(file_path),
#         created_by=created_by,
#     )
#     db.add(new_version)
#     db.flush()

#     # 3. Fetch previous workflow steps
#     old_steps = (
#         db.query(DocumentApprovalStep)
#         .filter(
#             DocumentApprovalStep.document_id == document_id,
#             DocumentApprovalStep.version_id == last_version.id,
#         )
#         .order_by(DocumentApprovalStep.step_order)
#         .all()
#     )

#     # ===== IMPORTANT CHANGE =====
#     # Step 1 -> uploader self approved
#     uploader_step = old_steps[0]
#     db.add(
#         DocumentApprovalStep(
#             document_id=document_id,
#             version_id=new_version.id,
#             step_order=1,
#             assigned_to=uploader_step.assigned_to,
#             approver_type=uploader_step.approver_type,
#             status="APPROVED",
#             action_at=datetime.utcnow(),
#         )
#     )

#     # Remaining hierarchy
#     step_order = 2
#     for step in old_steps[1:]:
#         db.add(
#             DocumentApprovalStep(
#                 document_id=document_id,
#                 version_id=new_version.id,
#                 step_order=step_order,
#                 assigned_to=step.assigned_to,
#                 approver_type=step.approver_type,
#                 status="PENDING" if step_order == 2 else "WAITING",
#             )
#         )
#         step_order += 1

#     # Create workflow run
#     workflow = DocumentWorkflowRun(
#         document_id=document_id,
#         version_id=new_version.id,
#         workflow_status="IN_PROGRESS",
#     )
#     db.add(workflow)

#     # Update document state
#     document = db.query(Document).filter(Document.id == document_id).first()

#     # if no hierarchy beyond uploader => auto approve whole document
#     if len(old_steps) == 1:
#         document.status = "APPROVED"
#         document.current_version = new_version_number
#         document.current_assignee_id = None
#         document.current_step_order = None
#         db.commit()
#         return {
#             "message": "Document reuploaded and auto-approved.",
#             "version": new_version_number,
#             "file_path": file_path,
#             "version_id": new_version.id,
#         }

#     # otherwise pending on next approver
#     document.status = "REUPLOADED"  # matches SUBMITTED flow
#     document.current_version = new_version_number
#     document.current_step_order = 2
#     document.current_assignee_id = old_steps[1].assigned_to

#     db.commit()

#     return {
#         "message": "Document reuploaded and workflow restarted.",
#         "version": new_version_number,
#         "file_path": file_path,
#         "version_id": new_version.id,
#     }


def reupload_document_version(
    db: Session, *, document_id: int, file_path: str, file_name: str, created_by: int
):
    # 1. Fetch last version
    last_version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    if not last_version:
        raise HTTPException(500, "No base version found")

    # 2. Assign new version number
    new_version_number = last_version.version_number + 1

    # 3. Create version entry
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

    # 4. Get previous approval steps
    old_steps = (
        db.query(DocumentApprovalStep)
        .filter(
            DocumentApprovalStep.document_id == document_id,
            DocumentApprovalStep.version_id == last_version.id,
        )
        .order_by(DocumentApprovalStep.step_order)
        .all()
    )

    if not old_steps:
        raise HTTPException(500, "Approval hierarchy not found")

    # ==== Step 1 → uploader auto approved ====
    uploader_step = old_steps[0]
    db.add(
        DocumentApprovalStep(
            document_id=document_id,
            version_id=new_version.id,
            step_order=1,
            assigned_to=uploader_step.assigned_to,
            approver_type=uploader_step.approver_type,
            status="APPROVED",
            action_at=datetime.utcnow(),
        )
    )

    # ==== Remaining hierarchy =====
    order = 2
    for s in old_steps[1:]:
        db.add(
            DocumentApprovalStep(
                document_id=document_id,
                version_id=new_version.id,
                step_order=order,
                assigned_to=s.assigned_to,
                approver_type=s.approver_type,
                status="PENDING"
            )
        )
        order += 1

    # ==== Create workflow for new version ====
    db.add(
        DocumentWorkflowRun(
            document_id=document_id,
            version_id=new_version.id,
            workflow_status="IN_PROGRESS",
        )
    )

    # ==== Update Document Root ====
    doc = db.query(Document).filter(Document.id == document_id).first()

    # if only uploader in chain -> auto approve
    if len(old_steps) == 1:
        doc.status = "APPROVED"
        doc.current_version = new_version_number
        doc.current_assignee_id = None
        doc.current_step_order = None
        db.commit()
        return {
            "message": "Document reuploaded and auto-approved.",
            "version": new_version_number,
            "version_id": new_version.id,
        }

    # Normal chain → now pending
    doc.status = "REUPLOADED"
    doc.current_version = new_version_number
    doc.current_step_order = 2
    doc.current_assignee_id = old_steps[1].assigned_to

    db.commit()

    return {
        "message": "Reuploaded successfully",
        "version": new_version_number,
        "version_id": new_version.id,
    }


# def get_approver_inbox(
#     db: Session,
#     current_user: dict,
#     search: str | None,
#     status: str | None,
#     page: int,
#     pagelimit: int,
# ):
#     user_id = current_user["user_id"]
#     offset = (page - 1) * pagelimit

#     # Base query on DOCUMENT (not versions)
#     base_query = (
#         db.query(Document, User)
#         .join(User, User.id == Document.uploaded_by)
#         .filter(
#             Document.is_delete.is_(False),
#             Document.status.in_(["REJECTED", "REUPLOADED", "APPROVED", "SUBMITTED"]),
#         )
#     )

#     total = base_query.count()

#     rows = (
#         base_query.order_by(Document.created_at.desc())
#         .offset(offset)
#         .limit(pagelimit)
#         .all()
#     )

#     data = []

#     for doc, uploader in rows:

#         # ---- fetch first version for FILE NAME display ----
#         first_version = (
#             db.query(DocumentVersion)
#             .filter(DocumentVersion.document_id == doc.id)
#             .order_by(DocumentVersion.version_number.asc())
#             .first()
#         )

#         # ---- fetch latest version workflow status for USER ----
#         latest_version = (
#             db.query(DocumentVersion)
#             .filter(DocumentVersion.document_id == doc.id)
#             .order_by(DocumentVersion.version_number.desc())
#             .first()
#         )

#         # find step assigned to user in latest version
#         my_step = (
#             db.query(DocumentApprovalStep)
#             .filter(
#                 DocumentApprovalStep.document_id == doc.id,
#                 DocumentApprovalStep.version_id == latest_version.id,
#                 DocumentApprovalStep.assigned_to == user_id,
#             )
#             .first()
#         )

#         if not my_step:
#             continue  # user not involved in latest version, skip

#         viewer_status = my_step.status  # <-- SIMPLE STATUS

#         if search:
#             if first_version and search.lower() not in first_version.file_name.lower():
#                 continue

#         if status:
#             if viewer_status != status.upper():
#                 continue

#         data.append(
#             {
#                 "document_id": doc.id,
#                 "file_name": first_version.file_name if first_version else None,
#                 "version_number": latest_version.version_number if latest_version else None,
#                 "status": viewer_status,  # <-- EXACT SIMPLE STATUS
#                 "uploaded_by": {"user_id": uploader.id, "name": uploader.name},
#                 "submitted_at": doc.created_at,
#             }
#         )

#     return {
#         "statusCode": 200,
#         "message": "Inbox fetched successfully",
#         "page": page,
#         "pagelimit": pagelimit,
#         "total": total,
#         "data": data,
#     }

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

    # fetch all documents for this company (no status restriction)
    docs = (
        db.query(Document, User)
        .join(User, User.id == Document.uploaded_by)
        .filter(Document.is_delete.is_(False))
        .order_by(Document.created_at.desc())
        .all()
    )

    data = []

    for doc, uploader in docs:

        # -------- FIRST VERSION (identity filename) --------
        first_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.asc())
            .first()
        )
        if not first_version:
            continue

        # -------- LATEST VERSION (workflow version) --------
        latest_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.desc())
            .first()
        )
        if not latest_version:
            continue

        # -------- FETCH ALL STEPS FOR LATEST VERSION --------
        steps = (
            db.query(DocumentApprovalStep)
            .filter(
                DocumentApprovalStep.document_id == doc.id,
                DocumentApprovalStep.version_id == latest_version.id,
            )
            .order_by(DocumentApprovalStep.step_order)
            .all()
        )
        if not steps:
            continue

        # -------- FIND MY STEP --------
        my_step = next((s for s in steps if s.assigned_to == user_id), None)
        if not my_step:
            continue   # user not in this workflow

        # -------- SEQUENTIAL BLOCKING LOGIC --------
        previous_steps = [s for s in steps if s.step_order < my_step.step_order]
        blocked = any(s.status != "APPROVED" for s in previous_steps)

        if blocked:
            continue   # cannot view yet (example: company admin before dept head)

        viewer_status = my_step.status   # exact PENDING/APPROVED/REJECTED

        # -------- SEARCH FILTER --------
        if search:
            if search.lower() not in first_version.file_name.lower():
                continue

        # -------- STATUS FILTER --------
        if status:
            if viewer_status != status.upper():
                continue

        data.append(
            {
                "document_id": doc.id,
                "file_name": first_version.file_name,
                "version_number": latest_version.version_number,
                "status": viewer_status,
                "uploaded_by": {
                    "user_id": uploader.id,
                    "name": uploader.name,
                },
                "submitted_at": doc.created_at,
            }
        )

    total = len(data)

    # -------- PAGINATION AFTER FILTERING --------
    paginated = data[offset: offset + pagelimit]

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
