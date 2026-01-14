from sqlalchemy.orm import Session
from fastapi import HTTPException
from sqlalchemy import or_, cast,func, and_
from app.enum import UserRole, ROLE_ORDER
from datetime import datetime
from sqlalchemy.dialects.postgresql import TEXT
from app.models import DocumentVersion, Document, User, Department, DocumentApprovalStep,DocumentWorkflowRun

def get_documents_service(
    db: Session,
    current_user: dict,
    search: str | None,
    status: str | None,
    page: int,
    size: int,
):
    offset = (page - 1) * size

    # base: user's docs
    query = (
        db.query(Document)
        .filter(
            Document.is_delete.is_(False),
            Document.uploaded_by == current_user["user_id"],
        )
    )

    # filter by status if provided
    if status:
        query = query.filter(Document.status == status)

    total = query.count()

    documents = (
        query.order_by(Document.created_at.desc())
        .offset(offset)
        .limit(size)
        .all()
    )

    data = []

    for doc in documents:

        # ---------- FIRST VERSION (identity filename) ----------
        first_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.asc())
            .first()
        )

        # ---------- LATEST VERSION (current metadata) ----------
        latest_version = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.desc())
            .first()
        )

        if not latest_version:
            continue

        # ---------- SEARCH FILTER ----------
        if search:
            s = search.lower()
            # check on first name (identity)
            if first_version and s not in first_version.file_name.lower():
                continue

        data.append(
            {
                "document_id": doc.id,
                "status": doc.status,
                "current_version": doc.current_version,
                "version": {
                    "version_number": latest_version.version_number,
                    "file_name": first_version.file_name if first_version else latest_version.file_name,
                    "file_size_bytes": latest_version.file_size_bytes,
                    "tags": latest_version.tags,
                    "summary": latest_version.summary,
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

    role = UserRole(current_user["role"])  # normalize

    base_users = db.query(User).filter(
        User.company_id == company_id,
        User.is_active.is_(True),
        User.is_delete.is_(False),
        User.id != user_id,
    )

    dept = None
    print("current_user",current_user)
    if role == UserRole.COMPANY_ADMIN:
        return{
            "statusCode":200,
            "data":[{
                "user_id":current_user["user_id"],
                "role":current_user["role"],
                "name":current_user["name"],
                "is_department_head":False,
                "order":ROLE_ORDER[UserRole.COMPANY_ADMIN],
                "self":True
            }]
        }

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
    # === ADD SELF IN HIERARCHY (except company admin, handled earlier) ===
    self_entry = {
    "user_id": current_user["user_id"],
    "name": current_user["name"],
    "role": role,
    "is_department_head": current_user.get("is_department_head", False),
    "order": ROLE_ORDER[role],
    "self":True
    }

    data = [self_entry]

    for u in users:
        is_dept_head = (dept is not None and u.id == dept.head_user_id)

        if u.role == UserRole.COMPANY_ADMIN:
            effective_role = UserRole.COMPANY_ADMIN
        elif is_dept_head:
            effective_role = UserRole.DEPARTMENT_HEAD
        else:
            effective_role = UserRole.EMPLOYEE

        data.append({
            "user_id": u.id,
            "name": u.name,
            "role": effective_role,
            "is_department_head": is_dept_head,
            "order": ROLE_ORDER[effective_role],
        })

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
    print("\n===== ASSIGN DEBUG START =====")

    print("DOC_ID:", document_id)
    print("UPLOADER:", current_user["user_id"])
    print("ASSIGNEES:", assignee_ids)

    # ---- FIND DOCUMENT ----
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
        print("ERROR: Document not found")
        raise HTTPException(404, "Document not found")

    print("CURRENT DOCUMENT STATUS:", document.status)

    if document.status not in ("DRAFT", "REJECTED"):
        print("ERROR: Not Draft/Rejected")
        raise HTTPException(400, "Document already submitted for approval")

    # ---- GET VERSION ----
    version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    print("VERSION FOUND:", version.version_number if version else None)

    if not version:
        raise HTTPException(500, "Document version missing")

    # ---- NO ASSIGNEE → AUTO PUBLIC ----
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
        print("===== ASSIGN DEBUG END - AUTO PUBLIC =====")
        return

    # ---- CREATE WORKFLOW ----
    print("CREATING WORKFLOW RUN")
    workflow = DocumentWorkflowRun(
        document_id=document_id,
        version_id=version.id,
        workflow_status="SUBMITTED"
    )
    db.add(workflow)
    db.flush()
    print("WORKFLOW_RUN_ID:", workflow.id)

    # ---- STEP 1: AUTO-APPROVE UPLOADER ----
    print("\n=== UPLOADER ROLE DEBUG ===")
    print("TOKEN ROLE:", current_user["role"])
    print("TOKEN is_department_head:", current_user.get("is_department_head"))

    # Effective role for uploader
    if current_user["role"] == "COMPANY_ADMIN":
        effective_uploader_role = "COMPANY_ADMIN"
    elif current_user.get("is_department_head", False):
        effective_uploader_role = "DEPARTMENT_HEAD"
    else:
        effective_uploader_role = "EMPLOYEE"

    print("UPLOAD EFFECTIVE ROLE:", effective_uploader_role)

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

    # ---- VALIDATE ASSIGNEES ----
    print("\nVALIDATING ASSIGNEES")
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

    print("VALID USERS:", [u.id for u in users])

    if len(users) != len(set(assignee_ids)):
        print("ERROR: Invalid Assignees")
        raise HTTPException(400, "Invalid assignee(s)")

    user_map = {u.id: u for u in users}

    # ---- STEP 2..N ----
    print("\nINSERTING ASSIGNEE STEPS")
    for idx, user_id in enumerate(assignee_ids):
        status = "PENDING" if idx == 0 else "WAITING"
        print(f"STEP {step_order}: user={user_id}, status={status}")

        user = user_map[user_id]

        # ---- ROLE DETECTION DEBUG ----
        print(f"[DEBUG] USER DB => id={user.id}, role={user.role}")

        # Check token / DB flags for dept head
        # fallback via department table
        is_dept_head = False
        if hasattr(user, "is_department_head"):
            is_dept_head = getattr(user, "is_department_head", False)

        if not is_dept_head:
            dept = db.query(Department).filter(
                Department.id == user.department_id,
                Department.head_user_id == user.id
            ).first()
            if dept:
                is_dept_head = True

        print(f"[DEBUG] is_dept_head evaluated => {is_dept_head}")

        # determine final role
        if user.role == "COMPANY_ADMIN":
            effective_role = "COMPANY_ADMIN"
        elif is_dept_head:
            effective_role = "DEPARTMENT_HEAD"
        else:
            effective_role = "EMPLOYEE"

        print(f"[DEBUG] EFFECTIVE ROLE => {effective_role}")

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

    # ---- UPDATE DOCUMENT ----
    document.status = "SUBMITTED"
    document.current_assignee_id = assignee_ids[0]
    document.current_step_order = 2
    document.current_version = version.version_number
    workflow.workflow_status = "IN_PROGRESS"

    print("\nUPDATED DOCUMENT STATUS:", document.status)
    print("CURRENT_ASSIGNEE:", document.current_assignee_id)
    print("CURRENT_STEP_ORDER:", document.current_step_order)

    db.commit()

    # ---- FINAL DEBUG ----
    print("\nFINAL STEP ROWS:")
    steps = db.query(DocumentApprovalStep).filter(
        DocumentApprovalStep.document_id == document_id
    ).order_by(DocumentApprovalStep.step_order).all()

    for s in steps:
        print(" -> order:", s.step_order, "user:", s.assigned_to, "role:", s.approver_type, "status:", s.status)

    print("===== ASSIGN DEBUG END =====\n")
