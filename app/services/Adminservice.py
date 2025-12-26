from sqlalchemy.orm import Session
from datetime import datetime
from fastapi import HTTPException, BackgroundTasks
from jose import jwt
import os

from app.models import User, OTPLogin, Company
from app.enum import UserRole
from app.schemas import CreateCompanySchema
from app.helpers import otp_generate, otp_expiry, send_otp_email

SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
print("JWT_SECRET =", os.getenv("JWT_SECRET"))
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET is not set")

def request_otp_service(email: str, background_tasks: BackgroundTasks, db: Session):
    user = (
        db.query(User)
        .filter(User.email == email, User.is_active.is_(True))
        .first()
    )

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.query(OTPLogin).filter(
        OTPLogin.user_id == user.id,
        OTPLogin.is_used.is_(False)
    ).update({"is_used": True})

    otp = otp_generate()

    otp_entry = OTPLogin(
        user_id=user.id,
        otp_code=otp,
        expires_at=otp_expiry()
    )

    db.add(otp_entry)
    db.commit()

    background_tasks.add_task(send_otp_email, email, otp)

    return {"message": "OTP sent successfully"}

def verify_otp_service(email: str, otp: str, db: Session):
    user = (
        db.query(User)
        .filter(User.email == email, User.is_active.is_(True))
        .first()
    )

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    otp_entry = (
        db.query(OTPLogin)
        .filter(
            OTPLogin.user_id == user.id,
            OTPLogin.otp_code == otp,
            OTPLogin.is_used.is_(False),
            OTPLogin.expires_at > datetime.utcnow()
        )
        .order_by(OTPLogin.created_at.desc())
        .first()
    )

    if not otp_entry:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    otp_entry.is_used = True
    user.last_login_at = datetime.utcnow()
    db.commit()

    token = jwt.encode(
        {
            "user_id": user.id,
            "company_id": user.company_id,
            "role": user.role.value
        },
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    return {"token": token}

def create_company_service(
    payload: CreateCompanySchema,
    db: Session,
    current_user: dict
):
    try:
        if db.query(Company).filter(
            Company.contact_email == payload.contact_email,
            Company.is_delete.is_(False)
        ).first():
            raise HTTPException(
                status_code=400,
                detail="Company with this email already exists"
            )

        if db.query(User).filter(
            User.email == payload.contact_email,
            User.is_delete.is_(False)
        ).first():
            raise HTTPException(
                status_code=400,
                detail="User with this email already exists"
            )

        company = Company(
            name=payload.name,
            contact_person=payload.contact_person,
            contact_email=payload.contact_email,
            created_by=current_user["user_id"]
        )

        db.add(company)
        db.flush()  # get company.id

        user = User(
            company_id=company.id,
            name=payload.contact_person,
            email=payload.contact_email,
            role=UserRole.COMPANY_ADMIN,
            is_active=True
        )

        db.add(user)
        db.commit()

        return {
            "company_id": company.id,
            "company_admin_user_id": user.id
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

def list_companies_service(
    db: Session,
    current_user: dict,
    page: int = 1,
    size: int = 10
):
    offset = (page - 1) * size

    total = (
        db.query(Company)
        .filter(
            Company.created_by == current_user["user_id"],
            Company.is_delete.is_(False)
        )
        .count()
    )

    companies = (
        db.query(Company)
        .filter(
            Company.created_by == current_user["user_id"],
            Company.is_delete.is_(False)
        )
        .order_by(Company.created_at.desc())
        .offset(offset)
        .limit(size)
        .all()
    )

    return {
        "page": page,
        "size": size,
        "total": total,
        "data": companies
    }

def updateStatusCompany(
    companyId: int,
    is_active: bool,
    db: Session,
    user: dict
):
    company = db.query(Company).filter(
        Company.id == companyId,
        Company.is_delete.is_(False)
    ).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    company.is_active = is_active
    db.commit()
    db.refresh(company)

    return {
        "status": 200,
        "detail": "Company status updated successfully",
        "data": company
    }


def delete_company_service(companyId: int, db: Session, user: dict):
    company = db.query(Company).filter(
        Company.id == companyId,
        Company.is_delete.is_(False)
    ).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    try:
        company.is_delete = True
        company.is_active = False

        db.query(User).filter(
            User.company_id == company.id,
            User.is_active.is_(True)
        ).update(
            {
                "is_active": False,
                "is_delete": True
            },
            synchronize_session=False
        )

        db.commit()

        return {
            "status": 200,
            "detail": "Company and associated users deleted successfully"
        }

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to delete company"
        )
