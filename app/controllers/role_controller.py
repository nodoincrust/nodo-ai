from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.helpers import get_current_user, get_db
from app.schemas import RoleListRequest, RoleUpsertSchema
from app.services.role_service import (
    create_role_service,
    delete_role_service,
    get_role_service,
    list_modules_service,
    list_roles_service,
    reporting_options_service,
    update_role_service,
)

router = APIRouter(prefix="/nodo/roles")


@router.post("/list")
def list_roles(
    payload: RoleListRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_roles_service(payload, db, current_user)


@router.get("/modules")
def list_modules(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_modules_service(db, current_user)


@router.get("/reporting-options")
def reporting_options(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return reporting_options_service(db, current_user)


@router.get("/{roleId}")
def get_role(
    roleId: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_role_service(roleId, db, current_user)


@router.post("/")
def create_role(
    payload: RoleUpsertSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_role_service(payload, db, current_user)


@router.put("/{roleId}")
def update_role(
    roleId: int,
    payload: RoleUpsertSchema,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_role_service(roleId, payload, db, current_user)


@router.delete("/{roleId}")
def delete_role(
    roleId: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return delete_role_service(roleId, db, current_user)
