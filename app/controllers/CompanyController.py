from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from app.services.Companyservice import (
    add_dept_service,
    get_dept_list,
    updateStatusDept,
    delete_department_details,
    update_dept_details,
    search_depts,
    add_employee_service,
    update_employee_service,
    delete_employee_details,
    updateStatusEmployee,
    get_employee_list,
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
from app.helpers import get_current_user,employee_manage_guard
from app.enum import UserRole

router = APIRouter(prefix="/nodo/company")

print("in dept")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/addDept")
def add_dept(
    payload: CreateDepartmentSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    print("i am under")
    if current_user["role"] != UserRole.COMPANY_ADMIN.value:
        raise HTTPException(status_code=403, detail="Unauthorized access!")

    return add_dept_service(payload=payload, db=db, current_user=current_user)


@router.get("/getDeptList")
def dept_list(
    page: int = 1,
    size: int = 10,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):

    if current_user["role"] != UserRole.COMPANY_ADMIN.value:
        raise HTTPException(status_code=403, detail="Unauthorized access!")
    return get_dept_list(db=db, current_user=current_user, page=page, size=size)


@router.put("/updateDeptDetails/{deptId}")
def update_company(
    deptId: int,
    payload: UpdateDeptSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):

    if current_user["role"] != UserRole.COMPANY_ADMIN.value:

        raise HTTPException(status_code=403, detail="Unauthorized access!")
    return update_dept_details(
        deptId=deptId, payload=payload, db=db, current_user=current_user
    )


@router.put("/{deptId}/status")
def updDeptStatus(
    deptId: int,
    payload: UpdateDeptStatusSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user["role"] != UserRole.COMPANY_ADMIN.value:
        raise HTTPException(status_code=403, detail="Unauthorized access!")

    return updateStatusDept(
        deptId=deptId, is_active=payload.is_active, db=db, current_user=current_user
    )


@router.put("/deleteDept/{deptId}")
def delete_company(
    deptId: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):

    if current_user["role"] != UserRole.COMPANY_ADMIN.value:
        raise HTTPException(status_code=403, detail="Unauthorized access!")

    return delete_department_details(deptId=deptId, db=db, current_user=current_user)


@router.get("/search")
def search(
    query: str | None = Query(default=None, min_length=1),
    page: int = 1,
    size: int = 10,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user["role"] != UserRole.COMPANY_ADMIN.value:
        raise HTTPException(status_code=403, detail="Unauthorized access")
    return search_depts(query=query, page=page, size=size, db=db, user=user)


@router.post("/addEmployee")
def add_employee(
    payload: CreateEmployeeSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employee_manage_guard(current_user)

    return add_employee_service(payload, db, current_user)


@router.put("/updatemployee/{employee_id}")
def update_employee(
    employee_id: int,
    payload: UpdateEmployeeSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employee_manage_guard(current_user)

    return update_employee_service(employee_id, payload, db, current_user)


@router.put("/deleteEmployee/{empId}")
def delete_Employee(
    empId: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):

    employee_manage_guard(current_user)

    return delete_employee_details(empId=empId, db=db, current_user=current_user)


@router.put("/employee/{empId}/status")
def updDeptStatusEmp(
    empId: int,
    payload: UpdateEmployeeStatusSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    employee_manage_guard(current_user)

    return updateStatusEmployee(
        empId=empId, is_active=payload.is_active, db=db, current_user=current_user
    )


@router.get("/getEmpList")
def Emp_List(
    query: str | None = Query(default=None, min_length=1),
    page: int = 1,
    size: int = 10,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):

    employee_manage_guard(current_user)

    return get_employee_list(
        db=db, current_user=current_user, page=page, size=size, query=query
    )
