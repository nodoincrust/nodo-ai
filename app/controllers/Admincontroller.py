from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks,Query
from app.services.Adminservice import (
    request_otp_service,
    verify_otp_service,
    create_company_service,
    list_companies_service,
    updateStatusCompany,
    delete_company_service,
    update_company_details,
    search_companies
)
from app.db import SessionLocal
from app.schemas import VerifyOTPSchema, CreateCompanySchema, UpdateCompanyStatusSchema,UpdateCompanySchema
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


@router.put("/deleteCompany/{companyId}")
def delete_company(companyId:int,db: Session = Depends(get_db),user=Depends(get_current_user)):
    
    if user["role"] != UserRole.SYSTEM_ADMIN.value:
        raise HTTPException(status_code=403,detail="Unauthorized access!")
    
    return delete_company_service(companyId=companyId,db=db,user=user)


@router.put("/updateCompanyDetails/{companyId}")
def update_company(companyId:int,payload: UpdateCompanySchema,db: Session = Depends(get_db),user=Depends(get_current_user)):
    
    if user["role"] != UserRole.SYSTEM_ADMIN.value:
        
        raise HTTPException(
            status_code=403,
            detail="Unauthorized access!"
        )
    return update_company_details(companyId=companyId, payload=payload,db=db,user=user)


@router.get("/search")
def search(query: str | None = Query(default=None, min_length=1),page: int = 1,size: int = 10,user=Depends(get_current_user),db: Session = Depends(get_db)):
    if user["role"] != UserRole.SYSTEM_ADMIN.value:
        raise HTTPException(
            status_code=403,
            detail="Unauthorized access"
        )
    return search_companies(query=query, page=page,size=size,db=db, user=user)