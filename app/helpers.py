import random
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage
import os
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import HTTPException, Depends
from jose import jwt, JWTError
from app.enum import UserRole, ROLE_LEVEL
from app.models import Department, User
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.services.summary_service import summarizeDocument
from jobs_store import jobs


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret-key")
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
        server.login("avinash@incrustsoftware.com","Incrust@123")
        server.send_message(msg)


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
    name=payload.get("name")
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
        "name":name,
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

def run_summary_job(job_id: str, documentId: int):
    try:
        result = summarizeDocument(documentId)
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
