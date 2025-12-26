from sqlalchemy.orm import Session
from datetime import datetime
from fastapi import HTTPException, BackgroundTasks
from jose import jwt
import os
from sqlalchemy import or_,func
from app.models import User, OTPLogin, Company,Department
from app.enum import UserRole
from app.schemas import CreateDepartmentSchema,UpdateDeptSchema

def add_dept_service(payload:CreateDepartmentSchema,db:Session,current_user:dict):
    try:
        if db.query(Department).filter(
            Department.contact_email==payload.contact_email,
            Department.is_delete.is_(False)
        ).first():
            raise HTTPException(
                status_code=400,
                detail="Department with this email already exists"
            )
        if db.query(User).filter(
            User.email==payload.contact_email,
            User.is_delete.is_(False),
        ).first():
            raise HTTPException(
                status_code=400,
                detail="User with this email already exist"
            )
        
        department= Department(
            name=payload.name,
            contact_person=payload.contact_person,
            contact_email=payload.contact_email,
            contact=payload.contact,
            company_id=current_user["user_id"]
        )
        db.add(department)
        db.flush()
        
        user=User(
            company_id=current_user["user_id"],
            department_id=department.id, 
            name=payload.contact_person,
            email=payload.contact_email,
            role=UserRole.DEPARTMENT_HEAD,
            is_active=True
        )
        db.add(user)
        db.commit()
        
        return {
            "department id":department.id,
            "department_user_id":user.id
        }
        
    except HTTPException:
        db.rollback()
        raise

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


def get_dept_list(db: Session,current_user: dict,page: int = 1,size: int = 10):
        
    offset=(page-1) * size
    total = (db.query(Department)
             .filter(
            Department.company_id == current_user["user_id"],
            Department.is_delete.is_(False)
        )
        .count()
    )    
    
    departments = (
        db.query(Department)
        .filter(
            Department.company_id == current_user["user_id"],
            Department.is_delete.is_(False)
        )
        .order_by(Department.created_at.desc())
        .offset(offset)
        .limit(size)
        .all()
    )

    return {
        "page": page,
        "size": size,
        "total": total,
        "data": departments
    }
    
def updateStatusDept(deptId: int, is_active: bool, db: Session, current_user: dict):

    department = db.query(Department).filter(
        Department.id == deptId,
        Department.is_delete.is_(False)
    ).first()

    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    department.is_active = is_active

    users = db.query(User).filter(
        User.department_id == deptId,
        User.is_delete.is_(False)
    ).all()

    for user in users:
        user.is_active = is_active

    db.commit()
    db.refresh(department)

    return {
        "status": 200,
        "detail": "Department status updated successfully",
        "data": {
            "department_id": department.id,
            "is_active": department.is_active,
            "users_updated": len(users)
        }
    }

def delete_department_details(deptId: int, db: Session, current_user: dict):

    department = db.query(Department).filter(
        Department.id == deptId,
        Department.is_delete.is_(False)
    ).first()

    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    try:
        department.is_delete = True
        department.is_active = False

       
        users = db.query(User).filter(
            User.department_id == department.id,
            User.is_delete.is_(False)
        ).all()

        for user in users:
            user.is_active = False
            user.is_delete = True

        db.commit()

        return {
            "status": 200,
            "detail": "Department and associated users deleted successfully",
            "data": {
                "department_id": department.id,
                "users_deleted": len(users)
            }
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to delete department"
        )
def update_dept_details(
    deptId: int,
    payload: UpdateDeptSchema,
    db: Session,
    current_user: dict
):
    department = db.query(Department).filter(
        Department.id == deptId,
        Department.company_id == current_user["user_id"],
        Department.is_delete.is_(False)
    ).first()

    if not department:
        raise HTTPException(
            status_code=404,
            detail="Department not found for this company"
        )

    dept_admin = db.query(User).filter(
        User.department_id == department.id,
        User.company_id == current_user["user_id"],
        User.role == UserRole.DEPARTMENT_HEAD,
        User.is_delete.is_(False)
    ).first()

    if not dept_admin:
        raise HTTPException(
            status_code=404,
            detail="Department admin user not found"
        )

    if payload.name is not None:
        department.name = payload.name

    if payload.contact_person is not None:
        department.contact_person = payload.contact_person
        dept_admin.name = payload.contact_person

    if payload.contact_email is not None:
        dept_exist = db.query(Department).filter(
            Department.contact_email == payload.contact_email,
            Department.company_id == current_user["company_id"],
            Department.id != deptId,
            Department.is_delete.is_(False)
        ).first()

        if dept_exist:
            raise HTTPException(
                status_code=400,
                detail="Department with this email already exists in this company"
            )

        user_exist = db.query(User).filter(
            User.email == payload.contact_email,
            User.company_id == current_user["user_id"],
            User.id != dept_admin.id,
            User.is_delete.is_(False)
        ).first()

        if user_exist:
            raise HTTPException(
                status_code=400,
                detail="User with this email already exists in this company"
            )

        department.contact_email = payload.contact_email
        dept_admin.email = payload.contact_email

    try:
        db.commit()
        db.refresh(department)

        return {
            "status": 200,
            "detail": "Department updated successfully",
            "data": {
                "department_id": department.id,
                "company_id": department.company_id,
                "department_name": department.name,
                "contact_person": department.contact_person,
                "contact_email": department.contact_email
            }
        }

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to update department details"
        )
def search_depts(query, page, size, db, user):
    offset = (page - 1) * size

    base_query = db.query(Department).filter(Department.is_delete.is_(False))

    if query:
        query = query.strip().lower()
        search = f"%{query}%"

        base_query = base_query.filter(
            or_(
                func.lower(Department.name).like(search),
                func.lower(func.coalesce(Department.contact_person, "")).like(search)
            )
        )

    print(
        base_query.statement.compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    departments = base_query.all()
    print("RESULT COUNT:", len(departments))

    return departments
