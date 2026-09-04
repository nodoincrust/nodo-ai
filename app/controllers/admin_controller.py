from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.services.admin_service import (
    request_otp_service,
    verify_otp_service,
    create_company_service,
    list_companies_service,
    updateStatusCompany,
    delete_company_service,
    update_company_details,
)
from app.schemas import (
    VerifyOTPSchema,
    CreateCompanySchema,
    UpdateCompanyStatusSchema,
    UpdateCompanySchema,
    GetCompaniesRequest,
)
from sqlalchemy.orm import Session
from app.helpers import get_current_user, get_db
from app.permissions import require_menu_permission, require_system_scope

router = APIRouter(prefix="/nodo")


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
    require_system_scope(current_user)
    require_menu_permission(db, current_user, "companies", "add")
    return create_company_service(payload=payload, db=db, current_user=current_user)


@router.post("/getCompanies")
def companiesList(
    payload: GetCompaniesRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_system_scope(current_user)
    require_menu_permission(db, current_user, "companies", "view")
    return list_companies_service(
        db=db,
        current_user=current_user,
        page=payload.page,
        size=payload.pagelimit,
        search=payload.search,
        status=payload.status,
    )


@router.put("/companies/{companyId}/status")
def updCompanyStatus(
    companyId: int,
    payload: UpdateCompanyStatusSchema,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    require_system_scope(user)
    require_menu_permission(db, user, "companies", "edit")
    return updateStatusCompany(
        companyId=companyId, is_active=payload.is_active, db=db, user=user
    )


@router.delete("/deleteCompany/{companyId}")
def delete_company(
    companyId: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    require_system_scope(user)
    require_menu_permission(db, user, "companies", "delete")
    return delete_company_service(companyId=companyId, db=db, user=user)


@router.put("/updateCompanyDetails/{companyId}")
def update_company(
    companyId: int,
    payload: UpdateCompanySchema,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    require_system_scope(user)
    require_menu_permission(db, user, "companies", "edit")
    return update_company_details(
        companyId=companyId, payload=payload, db=db, user=user
    )
