from fastapi import APIRouter, Body, Depends, HTTPException, Query
from app.services.company_service import (
    add_employee_service,
    update_employee_service,
    delete_employee_details,
    get_employee_list,
    get_deptwise_employee,
)
from app.db import SessionLocal
from app.schemas import CreateEmployeeSchema, UpdateEmployeeSchema, getdeptEmployee
from sqlalchemy.orm import Session
from app.helpers import get_current_user
from app.permissions import require_company_scope, require_menu_permission

router = APIRouter(prefix="/nodo/department")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/addEmployee")
def add_employee(
    payload: CreateEmployeeSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "employees", "add")
    if not current_user.get("is_department_head"):
        raise HTTPException(status_code=403, detail="Unauthorized access")
    return add_employee_service(payload, db, current_user)


@router.put("/updatemployee/{employee_id}")
def update_employee(
    employee_id: int,
    payload: UpdateEmployeeSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "employees", "edit")
    if not current_user.get("is_department_head"):
        raise HTTPException(403, "Unauthorized access")
    return update_employee_service(employee_id, payload, db, current_user)


@router.put("/deleteEmployee/{empId}")
def delete_Employee(
    empId: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "employees", "delete")
    if not current_user.get("is_department_head"):
        raise HTTPException(403, "Unauthorized access")
    return delete_employee_details(empId=empId, db=db, current_user=current_user)


@router.get("/getEmpList")
def Emp_List(
    query: str | None = Query(default=None, min_length=1),
    page: int = 1,
    size: int = 10,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "employees", "view")
    if not current_user.get("is_department_head"):
        raise HTTPException(403, "Unauthorized access")
    return get_employee_list(
        db=db, current_user=current_user, page=page, size=size, query=query
    )


@router.post("/getDeptEmployees")
def get_dept_employee(
    payload: getdeptEmployee,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_company_scope(current_user)
    require_menu_permission(db, current_user, "employees", "view")
    return get_deptwise_employee(db, payload.department_id, payload.search)
