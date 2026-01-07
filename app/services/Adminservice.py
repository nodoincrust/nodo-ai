from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from fastapi import HTTPException, BackgroundTasks
from jose import jwt
import os
from sqlalchemy import or_, func
from app.models import (
    User,
    OTPLogin,
    Company,
    Department,
    RoleSidebarMapping,
    SidebarMenu,
)
from app.enum import UserRole, SIDEBAR_MENU
from app.schemas import CreateCompanySchema, UpdateCompanySchema
from app.helpers import (
    otp_generate,
    otp_expiry,
    send_otp_email,
    resolve_ui_role,
    gb_to_bytes,
    bytes_to_gb,
    bytes_to_mb,
)
from datetime import datetime, timedelta
from fastapi import HTTPException
from jose import jwt

SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
print("JWT_SECRET =", os.getenv("JWT_SECRET"))
expire = datetime.utcnow() + timedelta(weeks=1)
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET is not set")


def request_otp_service(email: str, background_tasks: BackgroundTasks, db: Session):
    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        db.query(OTPLogin).filter(
            OTPLogin.user_id == user.id, OTPLogin.is_used.is_(False)
        ).update({"is_used": True})

        # otp = otp_generate()
        otp = 1234

        otp_entry = OTPLogin(user_id=user.id, otp_code=otp, expires_at=otp_expiry())

        db.add(otp_entry)
        db.commit()

        background_tasks.add_task(send_otp_email, email, otp)

        return {"statusCode": 200, "message": "OTP sent successfully"}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to generate OTP")


def get_sidebar_for_user(db: Session, role: str):
    try:
        menus = (
            db.query(SidebarMenu)
            .join(RoleSidebarMapping)
            .filter(RoleSidebarMapping.role == role, SidebarMenu.is_active.is_(True))
            .order_by(SidebarMenu.sort_order.asc())
            .all()
        )

        return [
            {
                "id": m.menu_key,
                "label": m.label,
                "path": m.path,
                "icon": m.icon,
                "icon_active": m.icon_active,
            }
            for m in menus
        ]

    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load sidebar menus")


def verify_otp_service(email: str, otp: str, db: Session):
    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    otp_entry = (
        db.query(OTPLogin)
        .filter(
            OTPLogin.user_id == user.id,
            OTPLogin.otp_code == otp,
            OTPLogin.is_used.is_(False),
            OTPLogin.expires_at > datetime.utcnow(),
        )
        .order_by(OTPLogin.created_at.desc())
        .first()
    )

    if not otp_entry:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    try:
        otp_entry.is_used = True
        user.last_login_at = datetime.utcnow()
        db.commit()

        department = None
        print("department", user.department_id)
        print("company", user.company_id)
        if user.department_id:
            department = (
                db.query(Department)
                .filter(
                    Department.id == user.department_id,
                    Department.company_id == user.company_id,
                    Department.is_active.is_(True),
                    Department.is_delete.is_(False),
                )
                .first()
            )

        is_department_head = (
            department is not None and department.head_user_id == user.id
        )

        expire = datetime.utcnow() + timedelta(days=7)
        print("department", department)
        payload = {
            "user_id": user.id,
            "company_id": user.company_id,
            "role": user.role.value,
            "name": user.name,
            "email": user.email,
            "exp": expire,
            "is_department_head": is_department_head,
            "department_id": department.id if department else None,
        }

        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        ui_role = resolve_ui_role(payload)
        sidebar = get_sidebar_for_user(db, ui_role)
        # static implemetation
        if user.role.value in ["COMPANY_ADMIN", "EMPLOYEE"]:
            storage = {
                "is_storage_show": True,
                "total_space": 10,
                "used_space": 4,
                "used_percentage": 60,
            }
        else:
            storage = {"is_storage_show": False}

        return {
            "statusCode": 200,
            "message": "Login successful",
            "data": {
                "token": token,
                "sidebar": sidebar,
                "is_department_head": is_department_head,
                "department_id": department.id if department else None,
                "user": {
                    "name": user.name,
                    "email": user.email,
                    "role": user.role.value,
                    "storage": storage,
                },
            },
        }
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="OTP verification failed")


def create_company_service(
    payload: CreateCompanySchema, db: Session, current_user: dict
):
    if (
        db.query(Company)
        .filter(
            Company.contact_email == payload.contact_email,
            Company.is_delete.is_(False),
        )
        .first()
    ):
        raise HTTPException(
            status_code=400, detail="Company with this email already exists"
        )

    if (
        db.query(User)
        .filter(User.email == payload.contact_email, User.is_delete.is_(False))
        .first()
    ):
        raise HTTPException(
            status_code=400, detail="User with this email already exists"
        )

    try:
        total_space_bytes = gb_to_bytes(payload.total_space)

        company = Company(
            name=payload.name,
            contact_person=payload.contact_person,
            contact_email=payload.contact_email,
            contact_number=payload.contact_number,
            total_space=total_space_bytes,
            remaining_space=total_space_bytes,
            created_by=current_user["user_id"],
        )

        db.add(company)
        db.flush()

        user = User(
            company_id=company.id,
            name=payload.contact_person,
            email=payload.contact_email,
            role=UserRole.COMPANY_ADMIN,
            is_active=True,
        )

        db.add(user)
        db.commit()

        return {
            "statusCode": 200,
            "message": "Company added successfully",
            "data": {"company_id": company.id, "company_admin_user_id": user.id},
        }

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create company")


def list_companies_service(
    db: Session,
    current_user: dict,
    page: int = 1,
    size: int = 10,
    search: str | None = None,
    status:str | None = None
):
    offset = (page - 1) * size

    base_query = db.query(Company).filter(
        Company.created_by == current_user["user_id"],
        Company.is_delete.is_(False),
    )

    if search:
        search_term = f"%{search}%"
        base_query = base_query.filter(
            or_(
                Company.name.ilike(search_term),
                Company.contact_email.ilike(search_term),
            )
        )
    if status:
        if status.lower()=="active":
            base_query=base_query.filter(Company.is_active.is_(True))
        elif status.lower()=="inactive":
            base_query=base_query.filter(Company.is_active.is_(False))

    total = base_query.count()

    companies = (
        base_query.order_by(Company.created_at.desc()).offset(offset).limit(size).all()
    )

    return {
        "statusCode": 200,
        "message": (
            "Companies fetched successfully" if total > 0 else "No companies found"
        ),
        "page": page,
        "size": size,
        "total": total,
        "data": companies,
    }


def updateStatusCompany(companyId: int, is_active: bool, db: Session, user: dict):
    company = (
        db.query(Company)
        .filter(Company.id == companyId, Company.is_delete.is_(False))
        .first()
    )

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if company.created_by != user["user_id"]:
        raise HTTPException(
            status_code=403, detail="You are not allowed to update this company"
        )

    try:
        company.is_active = is_active
        db.commit()
        db.refresh(company)

        return {
            "statusCode": 200,
            "message": "Company status updated successfully",
            "data": {"company_id": company.id, "is_active": company.is_active},
        }

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update company status")


def delete_company_service(companyId: int, db: Session, user: dict):
    company = (
        db.query(Company)
        .filter(Company.id == companyId, Company.is_delete.is_(False))
        .first()
    )

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if company.created_by != user["user_id"]:
        raise HTTPException(
            status_code=403, detail="You are not allowed to delete this company"
        )

    try:
        company.is_delete = True
        company.is_active = False

        db.query(User).filter(
            User.company_id == company.id, User.is_delete.is_(False)
        ).update({"is_active": False, "is_delete": True}, synchronize_session=False)

        db.commit()

        return {"statusCode": 200, "message": "Company deleted successfully"}

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete company")


def update_company_details(
    companyId: int, payload: UpdateCompanySchema, db: Session, user: dict
):
    company = (
        db.query(Company)
        .filter(Company.id == companyId, Company.is_delete.is_(False))
        .first()
    )

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if company.created_by != user["user_id"]:
        raise HTTPException(
            status_code=403, detail="You are not allowed to update this company"
        )

    company_admin = (
        db.query(User)
        .filter(
            User.company_id == companyId,
            User.role == UserRole.COMPANY_ADMIN,
            User.is_delete.is_(False),
        )
        .first()
    )

    if not company_admin:
        raise HTTPException(status_code=404, detail="Company admin user not found")

    if payload.contact_email is not None:
        company_exists = (
            db.query(Company)
            .filter(
                Company.contact_email == payload.contact_email,
                Company.id != companyId,
                Company.is_delete.is_(False),
            )
            .first()
        )

        if company_exists:
            raise HTTPException(
                status_code=400, detail="Company with this email already exists"
            )

        user_exists = (
            db.query(User)
            .filter(
                User.email == payload.contact_email,
                User.id != company_admin.id,
                User.is_delete.is_(False),
            )
            .first()
        )

        if user_exists:
            raise HTTPException(
                status_code=400, detail="User with this email already exists"
            )

    try:
        if payload.name is not None:
            company.name = payload.name

        if payload.contact_number is not None:
            company.contact_number = payload.contact_number

        if payload.total_space is not None:
            company.total_space = payload.total_space

        if payload.is_active is not None:
            company.is_active = payload.is_active

        if payload.contact_person is not None:
            company.contact_person = payload.contact_person
            company_admin.name = payload.contact_person

        if payload.contact_email is not None:
            company.contact_email = payload.contact_email
            company_admin.email = payload.contact_email

        db.commit()
        db.refresh(company)

        return {
            "statusCode": 200,
            "message": "Company details updated successfully",
            "data": company,
        }

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update company details")


def search_companies(query, page, size, db, user):
    offset = (page - 1) * size

    base_query = db.query(Company).filter(
        Company.is_delete.is_(False), Company.created_by == user["user_id"]
    )

    if query:
        search = f"%{query.strip().lower()}%"
        base_query = base_query.filter(
            or_(
                func.lower(Company.name).like(search),
                func.lower(func.coalesce(Company.contact_person, "")).like(search),
            )
        )

    total = base_query.count()

    companies = (
        base_query.order_by(Company.created_at.desc()).offset(offset).limit(size).all()
    )

    return {
        "statusCode": 200,
        "message": "Companies fetched successfully" if total else "No companies found",
        "page": page,
        "size": size,
        "total": total,
        "data": companies,
    }
