from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from app.services.EmployeeService import (
    get_documents_service
)
from app.db import SessionLocal
from app.schemas import (
    CreateDepartmentSchema,
    UpdateDeptStatusSchema,
    UpdateDeptSchema,
    CreateEmployeeSchema,
    UpdateEmployeeSchema,
    UpdateEmployeeStatusSchema,
)
from sqlalchemy.orm import Session
from app.helpers import get_current_user, employee_manage_guard
from app.enum import UserRole

router = APIRouter(prefix="/nodo/employee")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/documents")
def getDocumentList(
    search: str | None = None,
    status: str | None = None,
    version: str | None = None,
    tag: str | None = None,
    page: int = 1,
    size: int = 10,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
  return get_documents_service(
      db=db,
      current_user=current_user,
      search=search,
      status=status,
      version=version,
      tag=tag,
      page=page,
      size=size
  )