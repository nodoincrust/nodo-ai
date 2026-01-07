from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.helpers import get_current_user, employee_manage_guard, company_admin_guard
from app.schemas import (
    CreateDepartmentSchema,
    UpdateDeptSchema,
    UpdateDeptStatusSchema,
    CreateEmployeeSchema,
    UpdateEmployeeSchema,
    UpdateEmployeeStatusSchema,
    getDepartments,
    GetEmployee,getDepartmentList,GetEmployeeList
)
from app.services.Companyservice import (
    add_dept_service,
    get_dept_list,
    update_dept_details,
    updateStatusDept,
    delete_department_details,
    search_depts,
    add_employee_service,
    update_employee_service,
    delete_employee_details,
    updateStatusEmployee,
    get_employee_list,get_list_department,get_all_employees
)

router = APIRouter(prefix="/nodo/company")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -------------------- DEPARTMENTS --------------------


@router.post("/addDepartments")
def add_department(
    payload: CreateDepartmentSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_admin_guard(current_user)
    return add_dept_service(payload, db, current_user)


@router.post("/getDepartments")
def list_departments(
    payload: getDepartments = Body(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_admin_guard(current_user)
    return get_dept_list(
        db,
        current_user,
        page=payload.page,
        size=payload.pagelimit,
        search=payload.search,
        status=payload.status,
    )

@router.post("/getDepartmentList")
def get_list_departments(
    payload:getDepartmentList,
    current_user=Depends(get_current_user),
    db:Session=Depends(get_db)
):
    company_admin_guard(current_user)
    return get_list_department(db,current_user,search=payload.search)

@router.put("/updateDepartment/{deptId}")
def update_department(
    deptId: int,
    payload: UpdateDeptSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_admin_guard(current_user)
    return update_dept_details(deptId, payload, db, current_user)


@router.put("/departments/{deptId}/status")
def update_department_status(
    deptId: int,
    payload: UpdateDeptStatusSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_admin_guard(current_user)
    return updateStatusDept(deptId, payload.is_active, db, current_user)


@router.delete("/deleteDepartment/{deptId}")
def delete_department(
    deptId: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_admin_guard(current_user)
    return delete_department_details(deptId, db, current_user)


@router.get("/departments/search")
def search_departments(
    query: str | None = Query(default=None, min_length=1),
    page: int = 1,
    size: int = 10,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_admin_guard(current_user)
    return search_depts(query, page, size, db, current_user)


# -------------------- EMPLOYEES --------------------


@router.post("/addEmployee")
def add_employee(
    payload: CreateEmployeeSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employee_manage_guard(current_user)
    return add_employee_service(payload, db, current_user)


@router.put("/updateEmployee/{employee_id}")
def update_employee(
    employee_id: int,
    payload: UpdateEmployeeSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employee_manage_guard(current_user)
    return update_employee_service(employee_id, payload, db, current_user)


@router.delete("/deleteEmployee/{empId}")
def delete_employee(
    empId: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employee_manage_guard(current_user)
    return delete_employee_details(empId, db, current_user)


@router.put("/employees/{empId}/status")
def update_employee_status(
    empId: int,
    payload: UpdateEmployeeStatusSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employee_manage_guard(current_user)
    return updateStatusEmployee(empId, payload.is_active, db, current_user)


@router.post("/getEmployees")
def list_employees(
    payload: GetEmployee,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employee_manage_guard(current_user)
    return get_employee_list(
        db,
        current_user,
        page=payload.page,
        size=payload.pagelimit,
        query=payload.search,
        status=payload.status
    )
@router.post("/getEmployeeList")
def get_list_employees(
    payload:GetEmployeeList,
    current_user=Depends(get_current_user),
    db:Session=Depends(get_db)
):
    employee_manage_guard(current_user)
    return get_all_employees(db,current_user,query=payload.search)
