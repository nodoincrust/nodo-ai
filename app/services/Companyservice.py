from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException
from sqlalchemy import or_, func
from app.models import User, Department
from app.enum import UserRole
from app.schemas import (
    CreateDepartmentSchema,
    UpdateDeptSchema,
    UpdateEmployeeSchema,
)
from app.helpers import get_employee_scoped


def add_dept_service(payload: CreateDepartmentSchema, db: Session, current_user: dict):
    try:
        head_user = None

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
                raise HTTPException(
                    status_code=400, detail="Invalid department head user"
                )
        existing_dept = (
            db.query(Department)
            .filter(
                Department.company_id == current_user["company_id"],
                Department.is_delete.is_(False),
                Department.name.ilike(payload.name.strip()),
            )
            .first()
        )
        if existing_dept:
            raise HTTPException(
                status_code=409, detail="Department with this name is already exist!"
            )

        department = Department(
            company_id=current_user["company_id"],
            name=payload.name,
            description=payload.description,
            head_user_id=payload.head_user_id,
            is_active=payload.is_active,
        )

        db.add(department)
        db.flush()

        if head_user:
            head_user.is_department_head = True

        db.commit()
        db.refresh(department)

        return {
            "statusCode": 200,
            "message": "Department created successfully",
            "data": {
                "department_id": department.id,
                "head_user_id": department.head_user_id,
            },
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create department")


def get_dept_list(
    db: Session,
    current_user: dict,
    page: int,
    size: int,
    search: str | None = None,
    status: str | None = None,
):
    offset = (page - 1) * size

    base_query = (
        db.query(Department)
        .options(joinedload(Department.head))
        .outerjoin(User, Department.head_user_id == User.id)
        .filter(
            Department.company_id == current_user["company_id"],
            Department.is_delete.is_(False),
        )
    )

    if search:
        search_term = f"%{search.strip().lower()}%"
        base_query = base_query.filter(
            or_(
                func.lower(Department.name).like(search_term),
                func.lower(User.email).like(search_term),
            )
        )
    if status:
        if status.lower() == "active":
            base_query = base_query.filter(Department.is_active.is_(True))
        elif status.lower() == "inactive":
            base_query = base_query.filter(Department.is_active.is_(False))

    total = base_query.count()

    departments = (
        base_query.order_by(Department.created_at.desc())
        .offset(offset)
        .limit(size)
        .all()
    )

    return {
        "statusCode": 200,
        "message": (
            "Departments fetched successfully" if total else "No departments found"
        ),
        "page": page,
        "size": size,
        "total": total,
        "data": [
            {
                "id": d.id,
                "company_id": d.company_id,
                "name": d.name,
                "description": d.description,
                "head_user_id": d.head_user_id,
                "head_name": d.head.name if d.head else None,
                "is_active": d.is_active,
                "created_at": d.created_at,
            }
            for d in departments
        ],
    }


def get_list_department(db: Session, current_user: dict, search: str | None = None):

    base_query = (
        db.query(Department)
        .options(joinedload(Department.head))
        .outerjoin(User, Department.head_user_id == User.id)
        .filter(
            Department.company_id == current_user["company_id"],
            Department.is_delete.is_(False),
        )
    )
    if search:
        search_term = f"%{search.strip().lower()}%"
        base_query = base_query.filter(
            or_(
                func.lower(Department.name).like(search_term),
                func.lower(User.email).like(search_term),
            )
        )

    total = base_query.count()
    departments = base_query.order_by(Department.created_at.desc()).all()

    return {
        "statusCode": 200,
        "message": (
            "Departments fetched successfully" if total else "No departments found"
        ),
        "total": total,
        "data": [
            {
                "id": d.id,
                "company_id": d.company_id,
                "name": d.name,
                "description": d.description,
                "head_user_id": d.head_user_id,
                "head_name": d.head.name if d.head else None,
                "is_active": d.is_active,
                "created_at": d.created_at,
            }
            for d in departments
        ],
    }


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
        raise HTTPException(status_code=404, detail="Department not found")

    try:
        department.is_active = is_active
        db.commit()
        db.refresh(department)

        return {
            "statusCode": 200,
            "message": "Department status updated successfully",
            "data": {"department_id": department.id, "is_active": department.is_active},
        }

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Failed to update department status"
        )

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
        # delete + deactivate department
        department.is_delete = True
        department.is_active = False
        department.head_user_id = None

        # cascade delete users inside department
        db.query(User).filter(
            User.department_id == department.id,
            User.company_id == current_user["company_id"],
            User.is_delete.is_(False),
        ).update(
            {
                "is_delete": True,
                "is_active": False,
                "department_id": None  # optional - break reference
            }
        )

        db.commit()
        db.refresh(department)

        return {
            "statusCode": 200,
            "message": "Department deleted successfully",
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
        raise HTTPException(status_code=404, detail="Department not found")

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
            raise HTTPException(status_code=400, detail="Invalid department head user")

        department.head_user_id = payload.head_user_id
        head_user.department_id = department.id

    if payload.name is not None:
        department.name = payload.name

    if payload.description is not None:
        department.description = payload.description

    if payload.is_active is not None:
        department.is_active = payload.is_active
        
        db.query(User).filter(
            User.department_id==department.id,
            User.company_id==current_user["company_id"],
            User.is_delete.is_(False)
        ).update({"is_active":payload.is_active})

    try:
        db.commit()
        db.refresh(department)

        return {
            "statusCode": 200,
            "message": "Department details updated successfully",
            "data": {
                "department_id": department.id,
                "head_user_id": department.head_user_id,
                "is_active": department.is_active,
            },
        }

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update department")


def add_employee_service(payload, db: Session, current_user: dict):

    existing_user = (
        db.query(User)
        .filter(User.email == payload.email, User.is_delete.is_(False))
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=400, detail="User with this email already exists"
        )

    if current_user["role"] == UserRole.COMPANY_ADMIN.value:
        if not payload.department_id:
            raise HTTPException(status_code=400, detail="department_id is required")
        department_id = payload.department_id
    else:
        department_id = current_user["department_id"]

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
            raise HTTPException(status_code=400, detail="Invalid department")

    if payload.reports_to:
        if payload.reports_to == current_user["user_id"]:
            raise HTTPException(
                status_code=400, detail="User cannot report to themselves"
            )

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
            raise HTTPException(status_code=400, detail="Invalid reporting manager")

        if current_user.get("is_department_head"):
            if manager.department_id != current_user["department_id"]:
                raise HTTPException(
                    status_code=403,
                    detail="You can assign reporting manager only from your department",
                )

    try:
        user = User(
            name=payload.name,
            email=payload.email,
            company_id=current_user["company_id"],
            department_id=department_id,
            reports_to=payload.reports_to,
            designation=payload.designation,
            role=UserRole.EMPLOYEE,
            is_active=payload.is_active,
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "statusCode": 200,
            "message": "Employee added successfully",
            "data": {"user_id": user.id, "department_id": user.department_id},
        }

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to add employee")


def update_employee_service(
    employee_id: int, payload: UpdateEmployeeSchema, db: Session, current_user: dict
):
    employee = get_employee_scoped(db, employee_id, current_user)

    if payload.name is not None:
        employee.name = payload.name
    if payload.is_active is not None:
        employee.is_active = payload.is_active

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
            raise HTTPException(status_code=400, detail="Email already in use")

        employee.email = payload.email

    if payload.department_id is not None:
        if current_user["role"] != UserRole.COMPANY_ADMIN.value:
            raise HTTPException(
                status_code=403, detail="Only company admin can change department"
            )

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
            raise HTTPException(status_code=400, detail="Invalid department")

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
            raise HTTPException(status_code=400, detail="Invalid reporting manager")

        if current_user.get("is_department_head"):
            if manager.department_id != current_user["department_id"]:
                raise HTTPException(
                    status_code=403, detail="Manager must be from same department"
                )

        employee.reports_to = payload.reports_to

    if payload.designation is not None:
        if current_user["role"] != UserRole.COMPANY_ADMIN.value:
            raise HTTPException(
                status_code=403, detail="Only company admin can update designation"
            )

        employee.designation = payload.designation

    try:
        db.commit()
        db.refresh(employee)

        return {
            "statusCode": 200,
            "message": "Employee details updated successfully",
            "data": {"id": employee.id, "name": employee.name, "email": employee.email},
        }

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update employee")


def delete_employee_details(empId: int, db: Session, current_user: dict):

    employee = get_employee_scoped(db, empId, current_user)

    try:
        employee.is_delete = True
        employee.is_active = False

        db.commit()

        return {"statusCode": 200, "message": "Employee deleted successfully"}

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete employee")


def updateStatusEmployee(empId: int, is_active: bool, db: Session, current_user: dict):

    employee = get_employee_scoped(db, empId, current_user)

    try:
        employee.is_active = is_active
        db.commit()
        db.refresh(employee)

        return {
            "statusCode": 200,
            "message": "Employee status updated successfully",
            "data": {"employee_id": employee.id, "is_active": employee.is_active},
        }

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update employee status")


def get_employee_list(
    db: Session,
    current_user: dict,
    page: int,
    size: int,
    query: str | None = None,
    status: str | None = None,
):
    page = max(page, 1)
    size = max(size, 1)
    offset = (page - 1) * size

    base_query = (
    db.query(
        User.id,
        User.name,
        User.email,
        User.is_active,
        User.department_id,
        User.designation,
        Department.name.label("department_name"),
    )
    .outerjoin(Department, Department.id == User.department_id)
    .filter(
        User.company_id == current_user["company_id"],
        User.is_delete.is_(False),
        User.role == UserRole.EMPLOYEE,
        User.id != current_user["user_id"],
    )
)

    if current_user.get("is_department_head"):
        base_query = base_query.filter(
            User.department_id == current_user["department_id"]
        )

    if query:
        query = query.strip()
        if query:
            search = f"%{query}%"
            base_query = base_query.filter(
                or_(
                    User.name.ilike(search),
                    User.email.ilike(search),
                )
            )
    if status:
        if status.lower() == "active":
            base_query = base_query.filter(User.is_active.is_(True))
        elif status.lower() == "inactive":
            base_query = base_query.filter(User.is_active.is_(False))

    total = base_query.order_by(None).count()

    employees = (
        base_query.order_by(User.created_at.desc()).offset(offset).limit(size).all()
    )

    return {
        "statusCode": 200,
        "message": "Employees fetched successfully" if total else "No employees found",
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
                "department_name": e.department_name,
                "role": e.designation,
            }
            for e in employees
        ],
    }


def get_all_employees(db: Session, current_user: dict, query: str | None = None):

    base_query = db.query(
        User.id,
        User.name,
        User.email,
    ).filter(
        User.company_id == current_user["company_id"],
        User.is_delete.is_(False),
        User.role == UserRole.EMPLOYEE,
        User.id != current_user["user_id"],
    )

    if current_user.get("is_department_head"):
        base_query = base_query.filter(
            User.department_id == current_user["department_id"]
        )

    if query:
        query = query.strip()
        if query:
            search = f"%{query}%"
            base_query = base_query.filter(
                or_(
                    User.name.ilike(search),
                    User.email.ilike(search),
                )
            )

    total = base_query.order_by(None).count()

    employees = base_query.order_by(User.created_at.desc()).all()

    return {
        "statusCode": 200,
        "message": "Employees fetched successfully" if total else "No employees found",
        "total": total,
        "data": [
            {
                "id": e.id,
                "name": e.name,
                "email": e.email,
            }
            for e in employees
        ],
    }
