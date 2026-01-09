from sqlalchemy.orm import Session
from fastapi import HTTPException
from sqlalchemy import or_, cast
from app.enum import UserRole, ROLE_ORDER
from sqlalchemy.dialects.postgresql import TEXT
from app.models import DocumentVersion, Document, User, Department, DocumentApprovalStep


def get_documents_service(
    db: Session,
    current_user: dict,
    search: str | None,
    status: str | None,
    page: int,
    size: int,
):
    offset = (page - 1) * size

    query = (
        db.query(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .filter(
            Document.is_delete.is_(False),
            Document.uploaded_by == current_user["user_id"],
        )
    )

    if status:
        query = query.filter(Document.status == status)
    if search:
        search_pattern = f"%{search}%"

        query = query.filter(
            or_(
                DocumentVersion.file_name.ilike(search_pattern),
                cast(Document.status, TEXT).ilike(search_pattern),
                DocumentVersion.tags.op("?")(search),
            )
        )

    total = query.count()

    results = (
        query.order_by(Document.created_at.desc()).offset(offset).limit(size).all()
    )

    data = []
    for doc, version in results:
        data.append(
            {
                "document_id": doc.id,
                "status": doc.status,
                "current_version": doc.current_version,
                "version": {
                    "version_number": version.version_number,
                    "file_name": version.file_name,
                    "file_size_bytes": version.file_size_bytes,
                    "tags": version.tags,
                    "summary": version.summary,
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


def get_user_hierarchy(db: Session, current_user: dict):
    hierarchy = []

    user = (
        db.query(User)
        .filter(User.id == current_user["user_id"], User.is_delete.is_(False))
        .first()
    )


def get_assignable_users(db: Session, current_user: dict):
    company_id = current_user["company_id"]
    department_id = current_user["department_id"]
    user_id = current_user["user_id"]
    role = current_user["role"]

    base_users = db.query(User).filter(
        User.company_id == company_id,
        User.is_active.is_(True),
        User.is_delete.is_(False),
        User.id != user_id,
    )

    dept = None

    if role == UserRole.COMPANY_ADMIN:
        users = base_users.all()

    elif role == UserRole.DEPARTMENT_HEAD:
        users = base_users.filter(
            or_(
                User.department_id == department_id,
                User.role == UserRole.COMPANY_ADMIN,
            )
        ).all()

    else:
        dept = (
            db.query(Department)
            .filter(
                Department.id == department_id,
                Department.company_id == company_id,
            )
            .first()
        )

        if not dept or not dept.head_user_id:
            raise HTTPException(status_code=400, detail="Department head not assigned")

        users = base_users.filter(
            or_(
                User.id == dept.head_user_id,
                User.role == UserRole.COMPANY_ADMIN,
            )
        ).all()

    data = []

    for u in users:
        is_dept_head = dept is not None and u.id == dept.head_user_id

        effective_role = (
            UserRole.COMPANY_ADMIN
            if u.role == UserRole.COMPANY_ADMIN
            else UserRole.DEPARTMENT_HEAD if is_dept_head else UserRole.EMPLOYEE
        )

        data.append(
            {
                "user_id": u.id,
                "name": u.name,
                "role": u.role,
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

    if not assignee_ids:
        raise HTTPException(400, "No assignees provided")

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

    db.query(DocumentApprovalStep).filter(
        DocumentApprovalStep.document_id == document.id
    ).delete(synchronize_session=False)

    for idx, user_id in enumerate(assignee_ids, start=1):
        user = user_map[user_id]

        db.add(
            DocumentApprovalStep(
                document_id=document.id,
                step_order=idx,
                assigned_to=user.id,
                approver_type=user.role,
                status="PENDING",
            )
        )

    document.status = "IN_REVIEW"
    document.current_step_order = 1
    document.current_assignee_id = assignee_ids[0]

    db.commit()
