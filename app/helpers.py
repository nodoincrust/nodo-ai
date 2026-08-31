import random
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage
import os
import uuid
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import HTTPException, Depends
from jose import jwt, JWTError
from app.enum import UserRole
from app.models import (
    Department,
    User,
    Document,
    DocumentSummary,
    DocumentVersion,
    AIDocument,
    ShareDocument,
)
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.services.summary_service import summarizeDocument
from jobs_store import jobs
from app.AIhelpers.s3_storage import generateSignedUrl


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret-key")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
ONLYOFFICE_JWT_SECRET = os.getenv("ONLYOFFICE_JWT_SECRET", "onlyoffice-secret-key")
ONLYOFFICE_SECRET = os.getenv("ONLYOFFICE_SECRET", "asdf1234!@yash-dev")
ALGORITHM = "HS256"
security = HTTPBearer()
GB = 1024**3
MB = 1024**2


def otp_generate():
    return str(random.randint(1000, 9999))


def otp_expiry(minutes=5):
    return datetime.utcnow() + timedelta(minutes=minutes)


def send_otp_email(to_email: str, otp: str):
    msg = EmailMessage()
    msg["Subject"] = "Login Otp"
    msg["from"] = "avinash@incrustsoftware.com"
    msg["to"] = to_email
    msg.set_content(f"Your otp is {otp}.It is valid upto 5 Minutes.")
    with smtplib.SMTP("smtpout.secureserver.net", 587) as server:
        server.starttls()
        # server.login("avinash@incrustsoftware.com","Incrust@123")
        # server.send_message(msg)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("user_id")
    company_id = payload.get("company_id")
    name = payload.get("name")
    role = payload.get("role")
    department_id = payload.get("department_id")
    if not user_id or not role:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    department = None
    if department_id:
        department = (
            db.query(Department)
            .filter(
                Department.id == department_id,
                Department.company_id == company_id,
                Department.is_active.is_(True),
                Department.is_delete.is_(False),
            )
            .first()
        )
    is_department_head = department is not None and department.head_user_id == user_id
    current_user = {
        "user_id": user_id,
        "company_id": company_id,
        "role": role,
        "name": name,
        "department_id": department.id if department else None,
        "is_department_head": is_department_head,
    }
    return current_user


def employee_manage_guard(current_user: dict):
    if current_user["role"] != UserRole.COMPANY_ADMIN.value and not current_user.get(
        "is_department_head"
    ):
        raise HTTPException(403, "Unauthorized access")


def get_employee_scoped(db: Session, emp_id: int, current_user: dict):
    query = db.query(User).filter(
        User.id == emp_id,
        User.company_id == current_user["company_id"],
        User.role == UserRole.EMPLOYEE,
        User.is_delete.is_(False),
    )
    if current_user.get("is_department_head"):
        query = query.filter(User.department_id == current_user["department_id"])
    employee = query.first()
    print("employee found:", employee)
    if not employee:
        raise HTTPException(404, "Employee not found or unauthorized")
    return employee


def resolve_ui_role(current_user: dict):
    if current_user["role"] == UserRole.SYSTEM_ADMIN:
        return UserRole.SYSTEM_ADMIN
    if current_user["role"] == UserRole.COMPANY_ADMIN:
        return UserRole.COMPANY_ADMIN
    if current_user.get("is_department_head"):
        return UserRole.DEPARTMENT_HEAD
    return UserRole.EMPLOYEE


def gb_to_bytes(gb: int | float) -> int:
    return int(gb * GB)


def bytes_to_gb(byte_size: int) -> float:
    return round(byte_size / GB, 2)


def bytes_to_mb(byte_size: int) -> float:
    return round(byte_size / MB, 2)


def resolve_hierarchy(db: Session, current_user: dict):
    company_id = current_user["company_id"]
    department_id = current_user.get("department_id")
    dept_head = None
    if department_id:
        department = (
            db.query(Department)
            .filter(
                Department.id == department_id,
                Department.company_id == company_id,
                Department.is_active.is_(True),
                Department.is_delete.is_(False),
            )
            .first()
        )
        if department and department.head_user_id:
            dept_head = (
                db.query(User)
                .filter(
                    User.id == department.head_user_id,
                    User.is_active.is_(True),
                    User.is_delete.is_(False),
                )
                .first()
            )
    company_head = (
        db.query(User)
        .filter(
            User.company_id == company_id,
            User.role == UserRole.COMPANY_ADMIN,
            User.is_active.is_(True),
            User.is_delete.is_(False),
        )
        .first()
    )
    return dept_head, company_head


def company_admin_guard(user: dict):
    if user["role"] != UserRole.COMPANY_ADMIN.value:
        raise HTTPException(status_code=403, detail="Unauthorized access")


def get_hierarchy_order(user: User, is_department_head: bool) -> int:
    if user.role == UserRole.COMPANY_ADMIN:
        return 3
    if is_department_head:
        return 2
    return 1


def run_summary_job(job_id: str, documentId: int, version: int):
    try:
        result = summarizeDocument(documentId, version)
        jobs[job_id] = {"status": "done", "result": result}
    except Exception as e:
        jobs[job_id] = {"status": "error", "error": str(e)}


def normalize_role(step):
    role = step.approver_type

    # company admin
    if role == "COMPANY_ADMIN" or str(role) == "COMPANY_ADMIN":
        return "Company Admin"

    # dept head
    if role == "DEPARTMENT_HEAD" or str(role) == "DEPARTMENT_HEAD":
        return "Department Head"

    # uploader
    return "Uploader"


def build_tracking_timeline(steps, document_status):
    timeline = []

    # === CASE: NO STEPS ===
    if not steps:
        if document_status == "DRAFT":
            timeline.append(
                {
                    "role": "UPLOADER",
                    "status": "DRAFT",
                    "display": "In Draft Mode",
                    "timestamp": None,
                }
            )
            return timeline, "DRAFT"

        if document_status == "APPROVED":
            timeline.append(
                {
                    "role": "EMPLOYEE",
                    "status": "APPROVED",
                    "display": "Self Approved",
                    "timestamp": None,
                }
            )
            return timeline, "APPROVED"

        # fallback
        timeline.append(
            {
                "role": "UNKNOWN",
                "status": document_status,
                "display": document_status.title(),
                "timestamp": None,
            }
        )
        return timeline, document_status

    rejected = False
    pending_found = False

    for s in steps:

        # use full step for normalize_role
        role = normalize_role(s)

        if s.status == "APPROVED":
            timeline.append(
                {
                    "role": role,
                    "status": "APPROVED",
                    "display": f"Approved by {role}",
                    "timestamp": s.action_at,
                }
            )

        elif s.status == "PENDING":
            pending_found = True
            timeline.append(
                {
                    "role": role,
                    "status": "PENDING",
                    "display": f"Pending on {role}",
                    "timestamp": None,
                }
            )
            break

        elif s.status == "REJECTED":
            rejected = True
            timeline.append(
                {
                    "role": role,
                    "status": "REJECTED",
                    "display": f"Rejected by {role}",
                    "timestamp": s.action_at,
                }
            )
            break

    if rejected:
        return timeline, "REJECTED"

    if all(s.status == "APPROVED" for s in steps):
        return timeline, "APPROVED"

    if pending_found:
        return timeline, "PENDING"

    return timeline, "IN_PROGRESS"


def base_shared_query(db):
    return (
        db.query(
            Document.id.label("id"),
            Document.current_version.label("version"),
            DocumentVersion.file_name.label("file_name"),
            DocumentSummary.tags.label("tags"),
            User.name.label("name"),
            ShareDocument.created_at.label("shared_at"),
        )
        .join(
            DocumentVersion,
            (DocumentVersion.document_id == Document.id)
            & (DocumentVersion.version_number == Document.current_version),
        )
        .join(ShareDocument, ShareDocument.document_id == Document.id)
        .join(User, User.id == ShareDocument.shared_by)  # ⭐ new join here
        .outerjoin(
            AIDocument,
            (AIDocument.document_id == Document.id)
            & (AIDocument.version_id == DocumentVersion.id),
        )
        .outerjoin(
            DocumentSummary,
            (DocumentSummary.ai_document_id == AIDocument.id)
            & (DocumentSummary.version_id == DocumentVersion.id),
        )
        .order_by(ShareDocument.created_at.desc())
    )


def build_onlyoffice_editor(details, current_user):
    file_info = details["file"]
    doc_info = details["document"]

    ext = file_info["file_name"].split(".")[-1].lower()

    documentType = {
        "docx": "word",
        "txt": "word",
        "xlsx": "cell",
        "xls": "cell",
        "csv": "cell",
        "ppt": "slide",
        "pptx": "slide",
        "pdf": "pdf",
    }.get(ext, "word")

    editable = doc_info["uploaded_by"] == current_user["user_id"] and doc_info[
        "status"
    ] in ["DRAFT", "REJECTED", "REUPLOADED"]

    file_token = generate_file_token(
        document_id=doc_info["id"],
        version=file_info["version_number"],
        user_id=current_user["user_id"],
        file_path=file_info["file_path"],
    )

    # OnlyOffice fetches the document straight from S3 via a presigned URL
    document_url = file_info.get("file_url") or generateSignedUrl(
        file_info["file_path"]
    )

    # Key must change whenever the stored file changes, otherwise OnlyOffice
    # serves its cached copy and edits appear lost after a save.
    revision = file_info.get("updated_at") or file_info.get("created_at")
    revision_stamp = int(revision.timestamp()) if revision else 0

    print("Uploaded by:", doc_info["uploaded_by"])
    print("Current user:", current_user["user_id"])
    print("Status:", doc_info["status"])

    config = {
        "documentType": documentType,
        "document": {
            "fileType": ext,
            "title": file_info["file_name"],
            "key": (
                f"{doc_info['id']}-{file_info['version_number']}-{revision_stamp}"
            ),
            "url": document_url,
            "permissions": {
                "edit": editable,
                "download": False,  # disable download permanently
                "print": False,  # disable print permanently
                "comment": False,
                "review": False,
                "copy": True,
                "protect": False,
                "chat": False,
                "fillForms": False,
                "modifyContentControl": False,
                "showReviewChanges": False,
                "info": False,
            },
        },
        "editorConfig": {
            "mode": "edit" if editable else "view",
            "user": {
                "id": str(current_user["user_id"]),
                "name": current_user.get("name", "User"),
            },
            "customization": {
                # Removes unwanted feature tabs
                "hideRightMenu": True,
                "plugins": False,
                "help": False,
                "feedback": False,
                "about": False,
                "collaboration": False,
                "protection": False,
                # Disable unwanted features
                "showReviewChanges": False,
                "comments": False,
                "spellcheck": True,
                "compactHeader": False,
                "autosave": True,
                "forcesave": True,
                # "features": {"featuresTips": False},
                "logo": {
                    "visible": False,
                },
                "suggestFeature": False,
            },
            "callbackUrl": (
                f"{BACKEND_BASE_URL}/nodo/newdocuments/onlyoffice/callback/"
                f"{doc_info['id']}"
            ),
        },
    }
    token = jwt.encode(config, ONLYOFFICE_SECRET, algorithm="HS256")
    config["token"] = token

    return config


FILE_TOKEN_STORE = {}


def generate_file_token(*, document_id, version, user_id, file_path):
    token = str(uuid.uuid4())
    FILE_TOKEN_STORE[token] = {
        "document_id": document_id,
        "version": version,
        "user_id": user_id,
        "file_path": file_path,  # ✅ REQUIRED
        "expires_at": datetime.utcnow() + timedelta(minutes=10),
    }
    print(file_path)
    print("Generated file token:", token)
    return token
