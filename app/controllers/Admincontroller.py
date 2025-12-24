from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.services.Adminservice import (
    request_otp_service,
    verify_otp_service,
    create_company_service,
    list_companies_service,
    updateStatusCompany,
)
from app.db import SessionLocal
from app.schemas import VerifyOTPSchema, CreateCompanySchema, UpdateCompanyStatusSchema
from sqlalchemy.orm import Session
from app.helpers import get_current_user
from app.enum import UserRole

router = APIRouter(prefix="/nodo")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/request-otp")
def request_otp(
    email: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    return request_otp_service(
        email=email,
        background_tasks=background_tasks,
        db=db,
    )


@router.post("/verify-otp")
def verify_otp(payload: VerifyOTPSchema, db: Session = Depends(get_db)):
    return verify_otp_service(email=payload.email, otp=payload.otp, db=db)


@router.post("/addCompany")
def addCompany(
    payload: CreateCompanySchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user["role"] != UserRole.SYSTEM_ADMIN.value:
        raise HTTPException(status_code=403, detail="Unauthorized access!")

    return create_company_service(payload=payload, db=db, current_user=current_user)


@router.get("/getCompanies")
def companiesList(
    page: int = 1,
    size: int = 10,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user["role"] != UserRole.SYSTEM_ADMIN.value:
        raise HTTPException(status_code=403, detail="Unauthorized access!")

    return list_companies_service(
        db=db,
        current_user=current_user,
        page=page,
        size=size,
    )


@router.put("/companies/{companyId}/status")
def updCompanyStatus(
    companyId: int,
    payload: UpdateCompanyStatusSchema,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    if user["role"] != UserRole.SYSTEM_ADMIN.value:
        raise HTTPException(status_code=403, detail="Unauthorized access!")

    return updateStatusCompany(
        companyId=companyId, is_active=payload.is_active, db=db, user=user
    )

