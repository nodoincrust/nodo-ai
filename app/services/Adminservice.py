from sqlalchemy.orm import Session
from datetime import datetime,timedelta
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
from app.helpers import otp_generate, otp_expiry, send_otp_email, resolve_ui_role,gb_to_bytes,bytes_to_gb,bytes_to_mb

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

    db.query(OTPLogin).filter(
        OTPLogin.user_id == user.id, OTPLogin.is_used.is_(False)
    ).update({"is_used": True})

    # otp = otp_generate()
    otp = 1234

    otp_entry = OTPLogin(user_id=user.id, otp_code=otp, expires_at=otp_expiry())

    db.add(otp_entry)
    db.commit()

    background_tasks.add_task(send_otp_email, email, otp)

    return {"message": "OTP sent successfully"}


def get_sidebar_for_user(db: Session, role: str):
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

    otp_entry.is_used = True
    user.last_login_at = datetime.utcnow()
    db.commit()

    dept = (
        db.query(Department)
        .filter(
            Department.head_user_id == user.id,
            Department.company_id == user.company_id,
            Department.is_active.is_(True),
            Department.is_delete.is_(False),
        )
        .first()
    )

    payload = {
        "user_id": user.id,
        "company_id": user.company_id,
        "role": user.role.value,
        "name":user.name,
        "email":user.email,
        "exp":expire,
        "is_department_head": bool(dept),
        "department_id": dept.id if dept else None,
    }
    user={
        "name":user.name,
        "email":user.email
    }

    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    ui_role = resolve_ui_role(payload)
    # sidebar = SIDEBAR_MENU.get(ui_role, [])
    sidebar = get_sidebar_for_user(db, ui_role)
    return {
        "token": token,
        "sidebar": sidebar,
        "is_department_head": payload["is_department_head"],
        "department_id": payload["department_id"],
        "user":user
    }


def create_company_service(
    payload: CreateCompanySchema, db: Session, current_user: dict
):
    try:
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
        total_space_bytes=gb_to_bytes(payload.total_space)
        company = Company(
            name=payload.name,
            contact_person=payload.contact_person,
            contact_email=payload.contact_email,
            total_space=total_space_bytes,
            remaining_space=total_space_bytes,
            created_by=current_user["user_id"],
        )

        db.add(company)
        db.flush()  # get company.id

        user = User(
            company_id=company.id,
            name=payload.contact_person,
            email=payload.contact_email,
            role=UserRole.COMPANY_ADMIN,
            is_active=True,
        )

        db.add(user)
        db.commit()

        return {"company_id": company.id, "company_admin_user_id": user.id}

    except HTTPException:
        db.rollback()
        raise

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


def list_companies_service(
    db: Session, current_user: dict, page: int = 1, size: int = 10
):
    offset = (page - 1) * size
    total = (
        db.query(Company)
        .filter(
            Company.created_by == current_user["user_id"], Company.is_delete.is_(False)
        )
        .count()
    )

    companies = (
        db.query(Company)
        .filter(
            Company.created_by == current_user["user_id"], Company.is_delete.is_(False)
        )
        .order_by(Company.created_at.desc())
        .offset(offset)
        .limit(size)
        .all()
    )
    
    data=[]
    for company in companies:
        data.append(
            {
                "id":company.id,
                "name":company.name,
                "contact_person":company.contact_person,
                "contact_email":company.contact_email,
                "total_space":bytes_to_gb(company.total_space),
                "remaining_space":bytes_to_mb(company.remaining_space),
                "is_active":company.is_active,
                "created_at":company.created_at
            }
        )

    return {"page": page, "size": size, "total": total, "data": data}


def updateStatusCompany(companyId: int, is_active: bool, db: Session, user: dict):
    company = (
        db.query(Company)
        .filter(Company.id == companyId, Company.is_delete.is_(False))
        .first()
    )

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    company.is_active = is_active
    db.commit()
    db.refresh(company)

    return {
        "status": 200,
        "detail": "Company status updated successfully",
        "data": company,
    }


def delete_company_service(companyId: int, db: Session, user: dict):
    company = (
        db.query(Company)
        .filter(Company.id == companyId, Company.is_delete.is_(False))
        .first()
    )

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    try:
        company.is_delete = True
        company.is_active = False

        db.query(User).filter(
            User.company_id == company.id, User.is_active.is_(True)
        ).update({"is_active": False, "is_delete": True}, synchronize_session=False)

        db.commit()

        return {
            "status": 200,
            "detail": "Company and associated users deleted successfully",
        }

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

    if payload.name is not None:
        company.name = payload.name

    if payload.contact_person is not None:
        company.contact_person = payload.contact_person
        company_admin.name = payload.contact_person

    if payload.contact_email is not None:
        company_exist = (
            db.query(Company)
            .filter(
                Company.contact_email == payload.contact_email,
                Company.id != companyId,
                Company.is_delete.is_(False),
            )
            .first()
        )

        if company_exist:
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

        company.contact_email = payload.contact_email
        company_admin.email = payload.contact_email
    try:
        db.commit()
        db.refresh(company)

        return {
            "status": 200,
            "detail": "Company and admin user updated successfully",
            "data": company,
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update company details")


def search_companies(query, page, size, db, user):
    offset = (page - 1) * size

    base_query = db.query(Company).filter(Company.is_delete.is_(False))

    if query:
        query = query.strip().lower()
        search = f"%{query}%"

        base_query = base_query.filter(
            or_(
                func.lower(Company.name).like(search),
                func.lower(func.coalesce(Company.contact_person, "")).like(search),
            )
        )

    print(base_query.statement.compile(compile_kwargs={"literal_binds": True}))

    companies = base_query.all()
    print("RESULT COUNT:", len(companies))

    return companies
