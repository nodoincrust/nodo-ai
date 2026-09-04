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
    Role,
)
from app.services.role_service import (
    get_company_admin_role,
    get_role_by_id,
    is_company_admin_role,
    walk_reporting_role_chain,
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
    """Users eligible for document assignment based on reporting-role chain.

    Walks upward from the caller's role. Levels with no active users are skipped.
    Company Admin is always included when present.
    """
    company_id = current_user["company_id"]
    department_id = current_user.get("department_id")
    user_id = current_user["user_id"]

    caller_role = None
    if current_user.get("role_id"):
        caller_role = get_role_by_id(db, current_user["role_id"])

    # Self entry
    self_order = 0
    self_role_name = current_user.get("role")
    if caller_role:
        self_role_name = caller_role.name

    data = [
        {
            "user_id": user_id,
            "name": current_user["name"],
            "role": self_role_name,
            "role_id": current_user.get("role_id"),
            "is_department_head": current_user.get("is_department_head", False),
            "order": self_order,
            "self": True,
        }
    ]

    if caller_role and is_company_admin_role(caller_role):
        return {"statusCode": 200, "data": data}

    chain = walk_reporting_role_chain(db, caller_role) if caller_role else []

    # Legacy fallback when role_id not linked yet
    if not chain:
        role = UserRole(current_user["role"])
        if role == UserRole.COMPANY_ADMIN:
            return {"statusCode": 200, "data": data}
        admin_role = get_company_admin_role(db, company_id)
        if admin_role:
            chain = [admin_role]
        if role == UserRole.EMPLOYEE:
            dh = (
                db.query(Role)
                .filter(
                    Role.company_id == company_id,
                    Role.template_key == "DEPARTMENT_HEAD",
                    Role.is_delete.is_(False),
                )
                .first()
            )
            if dh:
                chain = [dh] + chain

    order = 1
    seen_users = {user_id}
    for role in chain:
        users_q = db.query(User).filter(
            User.company_id == company_id,
            User.role_id == role.id,
            User.is_active.is_(True),
            User.is_delete.is_(False),
            User.id != user_id,
        )
        # Prefer same department for non-admin roles when caller has a department
        if department_id and not is_company_admin_role(role):
            dept_users = users_q.filter(User.department_id == department_id).all()
            users = dept_users if dept_users else users_q.all()
        else:
            users = users_q.all()

        # Skip empty levels
        if not users:
            continue

        for u in users:
            if u.id in seen_users:
                continue
            seen_users.add(u.id)
            data.append(
                {
                    "user_id": u.id,
                    "name": u.name,
                    "role": role.name,
                    "role_id": role.id,
                    "is_department_head": role.template_key == "DEPARTMENT_HEAD",
                    "order": order,
                    "self": False,
                }
            )
        order += 1

    return {
        "statusCode": 200,
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

    if current_user.get("role_id"):
        uploader_role = get_role_by_id(db, current_user["role_id"])
        effective_uploader_role = (
            uploader_role.name if uploader_role else current_user["role"]
        )
    elif current_user["role"] == "COMPANY_ADMIN":
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

    # Sort assignees by reporting-role distance from Company Admin (lower first).
    admin_role = get_company_admin_role(db, current_user["company_id"])

    def _role_depth(user: User) -> int:
        role = get_role_by_id(db, user.role_id) if user.role_id else None
        if not role:
            if user.role == UserRole.COMPANY_ADMIN:
                return 999
            if user.role == UserRole.DEPARTMENT_HEAD:
                return 500
            return 100
        if is_company_admin_role(role):
            return 999
        depth = 0
        current = role
        seen = set()
        while current and current.reporting_role_id:
            if current.id in seen:
                break
            seen.add(current.id)
            depth += 1
            current = get_role_by_id(db, current.reporting_role_id)
        return depth

    ordered_ids = sorted(assignee_ids, key=lambda uid: _role_depth(user_map[uid]))

    # Ensure a Company Admin is last in the chain when one exists and was not selected.
    has_admin = any(
        is_company_admin_role(get_role_by_id(db, user_map[uid].role_id))
        if user_map[uid].role_id
        else user_map[uid].role == UserRole.COMPANY_ADMIN
        for uid in ordered_ids
    )
    if not has_admin and admin_role:
        admin_user = (
            db.query(User)
            .filter(
                User.company_id == current_user["company_id"],
                User.role_id == admin_role.id,
                User.is_active.is_(True),
                User.is_delete.is_(False),
            )
            .first()
        )
        if admin_user and admin_user.id not in ordered_ids:
            ordered_ids.append(admin_user.id)
            user_map[admin_user.id] = admin_user

    for user_id in ordered_ids:
        status = "PENDING"
        user = user_map[user_id]
        role = get_role_by_id(db, user.role_id) if user.role_id else None
        if role:
            effective_role = role.name
        elif user.role == UserRole.COMPANY_ADMIN:
            effective_role = "COMPANY_ADMIN"
        else:
            effective_role = user.role.value if hasattr(user.role, "value") else str(user.role)

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
    document.current_assignee_id = ordered_ids[0]
    document.current_step_order = 2
    document.current_version = version.version_number
    workflow.workflow_status = "IN_PROGRESS"
    db.commit()
