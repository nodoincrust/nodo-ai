"""Sidebar-menu–backed module helpers for Role Management.

Master list = sidebar_menus. Permissions reference sidebar_menu_id.
Template defaults use stable paths, resolved to menu rows at seed time.
"""

from sqlalchemy.orm import Session

from app.enum import RoleScope
from app.models import SidebarMenu

ACTIONS = ["add", "edit", "delete"]

# Legacy string keys (old role_permissions.module_key) → sidebar path
LEGACY_MODULE_TO_PATH = {
    "dashboard": "/dashboard",
    "companies": "/companies",
    "settings": "/settings",
    "departments": "/departments",
    "employees": "/employees",
    "users": "/users",
    "documents": "/documents",
    "dept_docs": "/documents/department",
    "approvals": "/approvals",
    "awaiting_approval": "/awaitingApproval",
    "bouquets": "/bouquet",
    "templates": "/templates",
    "shared_workspace": "/sharedworkspace",
    "role_management": "/role-management",
}

# Path → scope used when backfilling sidebar_menus.scope
PATH_SCOPE = {
    "/dashboard": "BOTH",
    "/companies": "SYSTEM",
    "/settings": "SYSTEM",
    "/role-management": "BOTH",
}


def _perm(path: str, *, view=True, add=False, edit=False, delete=False) -> dict:
    return {
        "path": path,
        "view": view,
        "add": add,
        "edit": edit,
        "delete": delete,
    }


def _full(path: str) -> dict:
    return _perm(path, view=True, add=True, edit=True, delete=True)


# Seed defaults by path (resolved against sidebar_menus at apply time)
SYSTEM_ADMIN_PERMS = [
    _full("/dashboard"),
    _full("/companies"),
    _full("/settings"),
    _full("/role-management"),
]

COMPANY_ADMIN_PERMS = [
    _full("/dashboard"),
    _full("/departments"),
    _full("/employees"),
    _full("/users"),
    _full("/documents"),
    _full("/documents/department"),
    _full("/approvals"),
    _full("/awaitingApproval"),
    _full("/bouquet"),
    _full("/templates"),
    _full("/sharedworkspace"),
    _full("/role-management"),
]

DEPARTMENT_HEAD_PERMS = [
    _perm("/dashboard", view=True),
    _full("/departments"),
    _full("/employees"),
    _full("/documents"),
    _full("/documents/department"),
    _full("/approvals"),
    _full("/awaitingApproval"),
    _full("/bouquet"),
    _full("/templates"),
    _full("/sharedworkspace"),
    _perm("/role-management", view=True),
]

EMPLOYEE_PERMS = [
    _perm("/dashboard", view=True),
    _perm("/documents", view=True, add=True, edit=True, delete=False),
    _perm("/sharedworkspace", view=True),
    _perm("/templates", view=True, add=True),
    _perm("/bouquet", view=True),
]


def menus_for_scope(db: Session, scope: RoleScope | str) -> list[SidebarMenu]:
    scope_val = scope.value if hasattr(scope, "value") else str(scope)
    if scope_val == RoleScope.SYSTEM.value:
        allowed = ("SYSTEM", "BOTH")
    else:
        allowed = ("COMPANY", "BOTH")
    return (
        db.query(SidebarMenu)
        .filter(SidebarMenu.is_active.is_(True), SidebarMenu.scope.in_(allowed))
        .order_by(SidebarMenu.sort_order.asc(), SidebarMenu.menu_key.asc())
        .all()
    )


def menu_payload(menu: SidebarMenu) -> dict:
    return {
        "sidebar_menu_id": menu.id,
        "label": menu.label,
        "actions": ACTIONS,
    }


def resolve_menu_by_path(db: Session, path: str) -> SidebarMenu | None:
    return (
        db.query(SidebarMenu)
        .filter(SidebarMenu.path == path, SidebarMenu.is_active.is_(True))
        .first()
    )


def resolve_menu_by_menu_key(db: Session, menu_key: int) -> SidebarMenu | None:
    return (
        db.query(SidebarMenu)
        .filter(SidebarMenu.menu_key == menu_key, SidebarMenu.is_active.is_(True))
        .first()
    )


def resolve_menu_by_id(db: Session, sidebar_menu_id: int) -> SidebarMenu | None:
    return (
        db.query(SidebarMenu)
        .filter(SidebarMenu.id == sidebar_menu_id, SidebarMenu.is_active.is_(True))
        .first()
    )
