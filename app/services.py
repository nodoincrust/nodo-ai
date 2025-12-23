from sqlalchemy.orm import Session
from datetime import datetime
from fastapi import HTTPException, BackgroundTasks
from jose import jwt
import os

from app.models import User, OTPLogin, Company
from app.enum import UserRole, UserStatus
from app.schemas import CreateCompanySchema
from app.helpers import otp_generate, otp_expiry, send_otp_email


SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret-key")
ALGORITHM = "HS256"



def request_otp_service(
    email: str,
    background_tasks: BackgroundTasks,
    db: Session,
):
    user = (
        db.query(User)
        .filter(
            User.email == email,
            User.status == UserStatus.ACTIVE,
        )
        .first()
    )

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.query(OTPLogin).filter(
        OTPLogin.user_id == user.id,
        OTPLogin.is_used.is_(False),
    ).update({"is_used": True})

    otp = otp_generate()

    otp_entry = OTPLogin(
        user_id=user.id,
        otp_code=otp,
        expires_at=otp_expiry(),
    )

    db.add(otp_entry)
    db.commit()

    background_tasks.add_task(send_otp_email, email, otp)

    return {"message": "OTP sent successfully"}



def verify_otp_service(
    email: str,
    otp: str,
    db: Session,
):
    user = (
        db.query(User)
        .filter(
            User.email == email,
            User.status == UserStatus.ACTIVE,
        )
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

    token = jwt.encode(
        {
            "user_id": user.id,
            "company_id": user.company_id,
            "role": user.role.value, 
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

    return {"access_token": token}


def create_company_service(
    payload: CreateCompanySchema,
    db: Session,
):
    try:
        if db.query(Company).filter(
            Company.contact_email == payload.contact_email
        ).first():
            raise HTTPException(
                status_code=400,
                detail="Company with this email already exists",
            )

        if db.query(User).filter(
            User.email == payload.contact_email
        ).first():
            raise HTTPException(
                status_code=400,
                detail="User with this email already exists",
            )

        company = Company(
            name=payload.name,
            contact_person=payload.contact_person,
            contact_email=payload.contact_email,
        )

        db.add(company)
        db.flush()  

        user = User(
            company_id=company.id,
            name=payload.contact_person,
            email=payload.contact_email,
            role=UserRole.COMPANY_ADMIN,
            status=UserStatus.ACTIVE,
        )

        db.add(user)
        db.commit()

        return {
            "company_id": company.id,
            "company_admin_user_id": user.id,
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
