from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from app.services.EmployeeService import (
    get_documents_service,
    get_assignable_users,
    assign_document,
)
from app.db import SessionLocal
from app.schemas import DocumentAssignSchema
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
    page: int = 1,
    size: int = 10,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return get_documents_service(
        db=db,
        current_user=current_user,
        search=search,
        status=status,
        page=page,
        size=size,
    )


@router.get("/assignable-hierarchy")
def get_assignable_hierarchy(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_assignable_users(db, current_user)


@router.post("/{document_id}/assign")
def assign_doc(
    document_id: int,
    payload: DocumentAssignSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    assign_document(
        db=db,
        document_id=document_id,
        assignee_ids=payload.assignee_ids,
        current_user=current_user,
    )

    return {
        "statusCode": 200,
        "message": "Document assigned successfully",
    }
