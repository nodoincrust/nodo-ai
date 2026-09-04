"""Central role_permissions checks against active sidebar_menus."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.enum import UserType
from app.models import RolePermission, SidebarMenu

# Stable paths already used by FE / sidebar_menus (do not change).
MENU = {
    "companies": "/companies",
    "settings": "/settings",
    "role_management": "/role-management",
    "departments": "/departments",
    "employees": "/employees",
    "documents": "/documents",
    "bouquets": "/bouquet",
    "templates": "/templates",
    "shared_workspace": "/sharedworkspace",
}

ACTIONS = ("view", "add", "edit", "delete")


def _user_type(current_user: dict) -> str:
    value = current_user.get("user_type")
    if value is None:
        return UserType.COMPANY.value
    return value.value if hasattr(value, "value") else str(value)


def require_system_scope(current_user: dict) -> None:
    if _user_type(current_user) != UserType.SYSTEM.value:
        raise HTTPException(status_code=403, detail="Unauthorized access!")


def require_company_scope(current_user: dict) -> None:
    if _user_type(current_user) == UserType.SYSTEM.value:
        raise HTTPException(status_code=403, detail="Unauthorized access!")
    if not current_user.get("company_id"):
        raise HTTPException(status_code=403, detail="Unauthorized access!")


def require_permission(
    db: Session,
    current_user: dict,
    menu_path: str,
    action: str,
) -> None:
    """Hard 403 unless caller's role_id has the action on an active sidebar menu."""
    if action not in ACTIONS:
        raise HTTPException(status_code=500, detail="Invalid permission action")

    role_id = current_user.get("role_id")
    if not role_id:
        raise HTTPException(status_code=403, detail="Unauthorized access!")

    menu = (
        db.query(SidebarMenu)
        .filter(
            SidebarMenu.path == menu_path,
            SidebarMenu.is_active.is_(True),
        )
        .first()
    )
    if not menu:
        raise HTTPException(status_code=403, detail="Unauthorized access!")

    perm = (
        db.query(RolePermission)
        .filter(
            RolePermission.role_id == role_id,
            RolePermission.sidebar_menu_id == menu.id,
        )
        .first()
    )
    if not perm or not bool(getattr(perm, action, False)):
        raise HTTPException(status_code=403, detail="Unauthorized access!")


def require_menu_permission(
    db: Session,
    current_user: dict,
    menu_key: str,
    action: str,
) -> None:
    path = MENU.get(menu_key)
    if not path:
        raise HTTPException(status_code=500, detail="Unknown menu permission key")
    require_permission(db, current_user, path, action)
