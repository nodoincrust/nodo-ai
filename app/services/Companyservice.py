from sqlalchemy.orm import Session
from datetime import datetime
from fastapi import HTTPException, BackgroundTasks
from jose import jwt
import os
from sqlalchemy import or_, func
from app.models import User, OTPLogin, Company, Department
from app.enum import UserRole
from app.schemas import CreateDepartmentSchema, UpdateDeptSchema, UpdateEmployeeSchema


def add_dept_service(payload: CreateDepartmentSchema, db: Session, current_user: dict):

    # validate head user (if provided)
    if payload.head_user_id is not None:
        head_user = (
            db.query(User)
            .filter(
                User.id == payload.head_user_id,
                User.company_id == current_user["user_id"],
                User.is_active.is_(True),
                User.is_delete.is_(False),
            )
            .first()
        )

        if not head_user:
            raise HTTPException(status_code=400, detail="Invalid department head user")

    department = Department(
        company_id=current_user["company_id"],
        name=payload.name,
        description=payload.description,
        head_user_id=payload.head_user_id,
    )

    try:
        db.add(department)
        db.commit()
        db.refresh(department)

        return {
            "status": 201,
            "detail": "Department created successfully",
            "data": {
                "department_id": department.id,
                "head_user_id": department.head_user_id,
            },
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


def get_dept_list(db: Session, current_user: dict, page: int, size: int):

    offset = (page - 1) * size

    base_query = db.query(Department).filter(
        Department.company_id == current_user["company_id"],
        Department.is_delete.is_(False),
    )

    total = base_query.count()

    departments = (
        base_query.order_by(Department.created_at.desc())
        .offset(offset)
        .limit(size)
        .all()
    )

    return {"page": page, "size": size, "total": total, "data": departments}


def updateStatusDept(deptId: int, is_active: bool, db: Session, current_user: dict):

    department = (
        db.query(Department)
        .filter(
            Department.id == deptId,
            Department.company_id == current_user["company_id"],
            Department.is_delete.is_(False),
        )
        .first()
    )

    if not department:
        raise HTTPException(404, "Department not found")

    department.is_active = is_active

    db.commit()
    db.refresh(department)

    return {
        "status": 200,
        "detail": "Department status updated successfully",
        "data": {"department_id": department.id, "is_active": department.is_active},
    }


def delete_department_details(deptId: int, db: Session, current_user: dict):

    department = (
        db.query(Department)
        .filter(
            Department.id == deptId,
            Department.company_id == current_user["company_id"],
            Department.is_delete.is_(False),
        )
        .first()
    )

    if not department:
        raise HTTPException(404, "Department not found")

    try:
        department.is_delete = True
        department.is_active = False
        department.head_user_id = None  # optional cleanup

        db.commit()

        return {
            "status": 200,
            "detail": "Department deleted successfully",
            "data": {"department_id": department.id},
        }

    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to delete department")


def update_dept_details(
    deptId: int, payload: UpdateDeptSchema, db: Session, current_user: dict
):

    department = (
        db.query(Department)
        .filter(
            Department.id == deptId,
            Department.company_id == current_user["company_id"],
            Department.is_delete.is_(False),
        )
        .first()
    )

    if not department:
        raise HTTPException(404, "Department not found")

    if payload.name is not None:
        department.name = payload.name

    if payload.description is not None:
        department.description = payload.description

    if payload.head_user_id is not None:
        head_user = (
            db.query(User)
            .filter(
                User.id == payload.head_user_id,
                User.company_id == current_user["company_id"],
                User.is_active.is_(True),
                User.is_delete.is_(False),
            )
            .first()
        )

        if not head_user:
            raise HTTPException(400, "Invalid department head user")

        department.head_user_id = payload.head_user_id

    try:
        db.commit()
        db.refresh(department)

        return {
            "status": 200,
            "detail": "Department updated successfully",
            "data": {
                "department_id": department.id,
                "head_user_id": department.head_user_id,
            },
        }

    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to update department")


def search_depts(query, page, size, db, user):

    offset = (page - 1) * size

    base_query = db.query(Department).filter(
        Department.company_id == user["company_id"], Department.is_delete.is_(False)
    )

    if query:
        search = f"%{query.strip().lower()}%"
        base_query = base_query.filter(func.lower(Department.name).like(search))

    return base_query.offset(offset).limit(size).all()


def add_employee_service(payload, db: Session, current_user: dict):

    # Email uniqueness
    existing_user = (
        db.query(User)
        .filter(User.email == payload.email, User.is_delete.is_(False))
        .first()
    )

    if existing_user:
        raise HTTPException(400, "User with this email already exists")

    # Decide department
    if current_user["role"] == UserRole.COMPANY_ADMIN.value:
        department_id = payload.department_id
    else:
        department_id = current_user["department_id"]

    # Validate department
    if department_id:
        dept = (
            db.query(Department)
            .filter(
                Department.id == department_id,
                Department.company_id == current_user["company_id"],
                Department.is_delete.is_(False),
            )
            .first()
        )

        if not dept:
            raise HTTPException(400, "Invalid department")

    # Validate manager (optional)
    if payload.reports_to:
        if payload.reports_to == current_user["user_id"]:
            raise HTTPException(400, "User cannot report to themselves")

        manager = (
            db.query(User)
            .filter(
                User.id == payload.reports_to,
                User.company_id == current_user["company_id"],
                User.is_delete.is_(False),
            )
            .first()
        )

        if not manager:
            raise HTTPException(400, "Invalid reporting manager")

        if current_user.get("is_department_head"):
            if manager.department_id != current_user["department_id"]:
                raise HTTPException(
                    403, "You can assign reporting manager only from your department"
                )

    # Create employee
    user = User(
        name=payload.name,
        email=payload.email,
        company_id=current_user["company_id"],
        department_id=department_id,
        reports_to=payload.reports_to,
        role=UserRole.EMPLOYEE,
        is_active=True,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "status": 201,
        "detail": "Employee added successfully",
        "data": {"user_id": user.id, "department_id": user.department_id},
    }


def update_employee_service(
    employee_id: int, payload: UpdateEmployeeSchema, db: Session, current_user: dict
):
    employee = (
        db.query(User)
        .filter(
            User.id == employee_id,
            User.company_id == current_user["company_id"],
            User.is_delete.is_(False),
        )
        .first()
    )

    if not employee:
        raise HTTPException(404, "Employee not found")

    if current_user.get("is_department_head"):
        if employee.department_id != current_user["department_id"]:
            raise HTTPException(403, "Unauthorized access")

    if payload.name is not None:
        employee.name = payload.name

    if payload.email is not None:
        email_exists = (
            db.query(User)
            .filter(
                User.email == payload.email,
                User.id != employee.id,
                User.is_delete.is_(False),
            )
            .first()
        )

        if email_exists:
            raise HTTPException(400, "Email already in use")

        employee.email = payload.email

    if payload.department_id is not None:
        if current_user["role"] != UserRole.COMPANY_ADMIN.value:
            raise HTTPException(403, "Only company admin can change department")

        dept = (
            db.query(Department)
            .filter(
                Department.id == payload.department_id,
                Department.company_id == current_user["company_id"],
                Department.is_delete.is_(False),
            )
            .first()
        )

        if not dept:
            raise HTTPException(400, "Invalid department")

        employee.department_id = payload.department_id

    if payload.reports_to is not None:
        manager = (
            db.query(User)
            .filter(
                User.id == payload.reports_to,
                User.company_id == current_user["company_id"],
                User.is_delete.is_(False),
            )
            .first()
        )

        if not manager:
            raise HTTPException(400, "Invalid reporting manager")

        if current_user.get("is_department_head"):
            if manager.department_id != current_user["department_id"]:
                raise HTTPException(403, "Manager must be from same department")

        employee.reports_to = payload.reports_to

    db.commit()
    db.refresh(employee)

    return {
        "status": 200,
        "detail": "Employee updated successfully",
        "data": {"id": employee.id, "name": employee.name, "email": employee.email},
    }


def delete_employee_details(empId: int, db: Session, current_user: dict):

    employee = (
        db.query(User)
        .filter(
            User.id == empId,
            User.company_id == current_user["company_id"],
            User.role == UserRole.EMPLOYEE,
            User.is_delete.is_(False),
        )
        .first()
    )

    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    # 🔐 Department head scope check
    if current_user.get("is_department_head"):
        if employee.department_id != current_user["department_id"]:
            raise HTTPException(status_code=403, detail="Unauthorized access")

    try:
        employee.is_delete = True
        employee.is_active = False

        db.commit()

        return {"status": 200, "detail": "Employee deleted successfully"}

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete employee")


def updateStatusEmployee(empId: int, is_active: bool, db: Session, current_user: dict):

    employee = (
        db.query(User)
        .filter(
            User.id == empId,
            User.company_id == current_user["company_id"],
            User.is_delete.is_(False),
        )
        .first()
    )

    if not employee:
        raise HTTPException(404, "Employee not found")

    employee.is_active = is_active

    db.commit()
    db.refresh(employee)

    return {
        "status": 200,
        "detail": "Employee status updated successfully",
        "data": {"employee_id": employee.id, "is_active": employee.is_active},
    }


def get_employee_list(
    db: Session, current_user: dict, page: int, size: int, query: str | None = None
):
    offset = (page - 1) * size

    base_query = db.query(
        User.id, User.name, User.email, User.is_active, User.department_id
    ).filter(
        User.company_id == current_user["company_id"],
        User.is_delete.is_(False),
        User.id != current_user["user_id"],
    )

    if current_user.get("is_department_head"):
        base_query = base_query.filter(
            User.department_id == current_user["department_id"]
        )

    if query:
        search = f"%{query.strip().lower()}%"
        base_query = base_query.filter(
            or_(func.lower(User.name).like(search), func.lower(User.email).like(search))
        )

    total = base_query.count()

    employees = (
        base_query.order_by(User.created_at.desc()).offset(offset).limit(size).all()
    )

    return {
        "page": page,
        "size": size,
        "total": total,
        "data": [
            {
                "id": e.id,
                "name": e.name,
                "email": e.email,
                "is_active": e.is_active,
                "department_id": e.department_id,
            }
            for e in employees
        ],
    }
