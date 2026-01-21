from sqlalchemy.orm import Session
from fastapi import HTTPException
from sqlalchemy import or_, cast, func, and_
from app.enum import UserRole, ROLE_ORDER
from datetime import datetime
from sqlalchemy.dialects.postgresql import TEXT
from app.models import (
    DocumentVersion,
    Document,
    User,
    Department,
    DocumentSummary,
    DocumentApprovalStep,
    DocumentWorkflowRun,
)


def normalize_role_name(r: str):
    if r == "EMPLOYEE":
        return "Uploader"
    if r == "DEPARTMENT_HEAD":
        return "Department Head"
    if r == "COMPANY_ADMIN":
        return "Company Admin"
    return r.title()


def get_documents_service(
    db: Session,
    current_user: dict,
    search: str | None,
    status: str | None,
    page: int,
    size: int,
):
    offset = (page - 1) * size

    # Base docs uploaded by user
    query = db.query(Document).filter(
        Document.is_delete.is_(False),
        Document.uploaded_by == current_user["user_id"],
    )

    if status:
        query = query.filter(Document.status == status)

    total = query.count()

    documents = (
        query.order_by(Document.created_at.desc()).offset(offset).limit(size).all()
    )

    data = []

    for doc in documents:

        # === FIRST VERSION (identity filename) ===
        first_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.asc())
            .first()
        )

        # === LATEST VERSION (current state) ===
        latest_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.desc())
            .first()
        )

        if not latest_version:
            continue

        # === FETCH SUMMARY & TAGS FOR LATEST VERSION ===
        summary_obj = (
            db.query(DocumentSummary)
            .filter(DocumentSummary.version_id == latest_version.id)
            .first()
        )

        summary_text = summary_obj.summary_text if summary_obj else None
        summary_tags = summary_obj.tags if summary_obj else []

        # === SEARCH (only filename + tags) ===
        if search:
            s = search.lower()
            matched = False

            # filename search (v1 identity)
            if first_version and s in first_version.file_name.lower():
                matched = True

            # tag search
            if not matched and summary_tags:
                if any(isinstance(t, str) and s in t.lower() for t in summary_tags):
                    matched = True

            if not matched:
                continue

        # === Compute Pending On Logic ===
        steps = (
            db.query(DocumentApprovalStep)
            .filter(
                DocumentApprovalStep.document_id == doc.id,
                DocumentApprovalStep.version_id == latest_version.id,
            )
            .order_by(DocumentApprovalStep.step_order)
            .all()
        )

        pending_on = None
        pending_step = next((s for s in steps if s.status == "PENDING"), None)

        if pending_step:
            pending_on = f"Pending on {pending_step.approver_type}"
        else:
            rejected = next((s for s in steps if s.status == "REJECTED"), None)
            if rejected:
                pending_on = f"Rejected by {rejected.approver_type}"
            else:
                if doc.status == "APPROVED":
                    pending_on = "Approved"
                elif doc.status == "REUPLOADED":
                    pending_on = "Reuploaded"
                else:
                    pending_on = "Draft"

        # === Build Response ===
        data.append(
            {
                "document_id": doc.id,
                "status": doc.status,
                "current_version": doc.current_version,
                "pending_on": pending_on,
                "version": {
                    "version_number": latest_version.version_number,
                    "file_name": (
                        first_version.file_name
                        if first_version
                        else latest_version.file_name
                    ),
                    "file_size_bytes": latest_version.file_size_bytes,
                    "tags": summary_tags,
                    "summary": summary_text,
                },
            }
        )

    return {
        "statusCode": 200,
        "message": "Documents fetched successfully",
        "page": page,
        "size": size,
        "total": total,
        "data": data,
    }


def get_assignable_users(db: Session, current_user: dict):
    company_id = current_user["company_id"]
    department_id = current_user["department_id"]
    user_id = current_user["user_id"]

    role = UserRole(current_user["role"])  # normalize

    base_users = db.query(User).filter(
        User.company_id == company_id,
        User.is_active.is_(True),
        User.is_delete.is_(False),
        User.id != user_id,
    )

    # Case 1: Company Admin can only assign themselves
    if role == UserRole.COMPANY_ADMIN:
        return {
            "statusCode": 200,
            "data": [
                {
                    "user_id": current_user["user_id"],
                    "role": current_user["role"],
                    "name": current_user["name"],
                    "is_department_head": False,
                    "order": ROLE_ORDER[UserRole.COMPANY_ADMIN],
                    "self": True,
                }
            ],
        }

    # Case 2: Department Head
    elif role == UserRole.DEPARTMENT_HEAD:
        users = base_users.filter(
            or_(
                User.department_id == department_id,
                User.role == UserRole.COMPANY_ADMIN,
            )
        ).all()

    # Case 3: Employee (fallback logic added here)
    else:
        dept = (
            db.query(Department)
            .filter(
                Department.id == department_id,
                Department.company_id == company_id,
            )
            .first()
        )

        # Fallback when no department head assigned
        if not dept or not dept.head_user_id:
            # fallback to company admins only (highest hierarchy)
            users = base_users.filter(User.role == UserRole.COMPANY_ADMIN).all()
        else:
            # normal: fallback head + company admin
            users = base_users.filter(
                or_(
                    User.id == dept.head_user_id,
                    User.role == UserRole.COMPANY_ADMIN,
                )
            ).all()

    # build self entry
    self_entry = {
        "user_id": current_user["user_id"],
        "name": current_user["name"],
        "role": role,
        "is_department_head": current_user.get("is_department_head", False),
        "order": ROLE_ORDER[role],
        "self": True,
    }

    data = [self_entry]

    for u in users:
        # determine effective role
        is_dept_head = dept is not None and u.id == getattr(dept, "head_user_id", None)

        if u.role == UserRole.COMPANY_ADMIN:
            effective_role = UserRole.COMPANY_ADMIN
        elif is_dept_head:
            effective_role = UserRole.DEPARTMENT_HEAD
        else:
            effective_role = UserRole.EMPLOYEE

        data.append(
            {
                "user_id": u.id,
                "name": u.name,
                "role": effective_role,
                "is_department_head": is_dept_head,
                "order": ROLE_ORDER[effective_role],
            }
        )

    return {
        "status": 200,
        "data": sorted(data, key=lambda x: x["order"]),
    }


def assign_document(
    db: Session,
    document_id: int,
    assignee_ids: list[int],
    current_user: dict,
):

    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.uploaded_by == current_user["user_id"],
            Document.is_delete.is_(False),
        )
        .first()
    )

    if not document:
        raise HTTPException(404, "Document not found")

    if document.status not in ("DRAFT", "REJECTED"):
        raise HTTPException(400, "Document already submitted for approval")

    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    if not version:
        raise HTTPException(500, "Document version missing")

    if not assignee_ids or len(assignee_ids) == 0:
        version.visibility = "COMPANY"
        version.public_at = datetime.utcnow()
        document.status = "APPROVED"
        document.current_assignee_id = None
        document.current_step_order = None

        workflow = DocumentWorkflowRun(
            document_id=document_id,
            version_id=version.id,
            workflow_status="COMPLETED",
            public_at=datetime.utcnow(),
        )
        db.add(workflow)
        db.commit()
        return

    workflow = DocumentWorkflowRun(
        document_id=document_id, version_id=version.id, workflow_status="SUBMITTED"
    )
    db.add(workflow)
    db.flush()

    if current_user["role"] == "COMPANY_ADMIN":
        effective_uploader_role = "COMPANY_ADMIN"
    elif current_user.get("is_department_head", False):
        effective_uploader_role = "DEPARTMENT_HEAD"
    else:
        effective_uploader_role = "EMPLOYEE"

    step_order = 1
    db.add(
        DocumentApprovalStep(
            document_id=document_id,
            version_id=version.id,
            step_order=step_order,
            assigned_to=current_user["user_id"],
            approver_type=effective_uploader_role,
            status="APPROVED",
            action_at=datetime.utcnow(),
        )
    )
    step_order += 1

    users = (
        db.query(User)
        .filter(
            User.id.in_(assignee_ids),
            User.company_id == current_user["company_id"],
            User.is_active.is_(True),
            User.is_delete.is_(False),
        )
        .all()
    )

    if len(users) != len(set(assignee_ids)):
        raise HTTPException(400, "Invalid assignee(s)")

    user_map = {u.id: u for u in users}

    for idx, user_id in enumerate(assignee_ids):
        status = "PENDING"
        user = user_map[user_id]
        is_dept_head = False
        if hasattr(user, "is_department_head"):
            is_dept_head = getattr(user, "is_department_head", False)

        if not is_dept_head:
            dept = (
                db.query(Department)
                .filter(
                    Department.id == user.department_id,
                    Department.head_user_id == user.id,
                )
                .first()
            )
            if dept:
                is_dept_head = True

        if user.role == "COMPANY_ADMIN":
            effective_role = "COMPANY_ADMIN"
        elif is_dept_head:
            effective_role = "DEPARTMENT_HEAD"
        else:
            effective_role = "EMPLOYEE"

        db.add(
            DocumentApprovalStep(
                document_id=document.id,
                version_id=version.id,
                step_order=step_order,
                assigned_to=user.id,
                approver_type=effective_role,
                status=status,
            )
        )
        step_order += 1

    document.status = "SUBMITTED"
    document.current_assignee_id = assignee_ids[0]
    document.current_step_order = 2
    document.current_version = version.version_number
    workflow.workflow_status = "IN_PROGRESS"
    db.commit()
