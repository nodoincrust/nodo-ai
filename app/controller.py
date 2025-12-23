from fastapi import APIRouter, Depends,HTTPException,BackgroundTasks
from app.services import request_otp_service, verify_otp_service,create_company_service
from app.db import SessionLocal
from app.schemas import VerifyOTPSchema,CreateCompanySchema
from sqlalchemy.orm import Session
from app.helpers import get_current_user

main_route = APIRouter(prefix="/nodo")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@main_route.post("/request-otp")
def request_otp(email: str,  background_tasks: BackgroundTasks,db: Session = Depends(get_db)):
    return request_otp_service(email=email, background_tasks=background_tasks ,db=db,)


@main_route.post("/verify-otp")
def verify_otp(payload: VerifyOTPSchema, db: Session = Depends(get_db)):
    return verify_otp_service(email=payload.email, otp=payload.otp, db=db)


@main_route.post("/addCompany")
def addCompany(payload:CreateCompanySchema,user=Depends(get_current_user),db: Session = Depends(get_db)):
    if user["role"] !="SYSTEM_ADMIN":
        raise HTTPException(
            status_code=403,
            detail="Unauthorized access!"
        )

    return create_company_service(payload=payload,db=db)