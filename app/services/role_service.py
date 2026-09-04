"""Role Management business logic (system + company scoped)."""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.enum import RoleScope, UserRole, UserType
from app.models import Role, RolePermission, SidebarMenu, User
from app.role_modules import (
    menu_payload,
    menus_for_scope,
    resolve_menu_by_id,
    resolve_menu_by_menu_key,
)
from app.services.role_seed_service import legacy_role_from_template_key


def resolve_caller_scope(current_user: dict) -> RoleScope:
    user_type = current_user.get("user_type")
    role = current_user.get("role")
    if user_type == UserType.SYSTEM.value or role == UserRole.SYSTEM_ADMIN.value:
        return RoleScope.SYSTEM
    return RoleScope.COMPANY


def _scope_filter(query, scope: RoleScope, company_id: int | None):
    query = query.filter(Role.scope == scope, Role.is_delete.is_(False))
    if scope == RoleScope.SYSTEM:
        return query.filter(Role.company_id.is_(None))
    if not company_id:
        raise HTTPException(status_code=400, detail="Company context required")
    return query.filter(Role.company_id == company_id)


def _role_load_options():
    return (
        joinedload(Role.permissions).joinedload(RolePermission.menu),
        joinedload(Role.reporting_role),
    )


def _get_role_in_scope(
    db: Session, role_id: int, scope: RoleScope, company_id: int | None
) -> Role:
    role = (
        db.query(Role)
        .options(*_role_load_options())
        .filter(Role.id == role_id, Role.is_delete.is_(False))
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.scope != scope:
        raise HTTPException(status_code=403, detail="Unauthorized access!")
    if scope == RoleScope.SYSTEM and role.company_id is not None:
        raise HTTPException(status_code=403, detail="Unauthorized access!")
    if scope == RoleScope.COMPANY and role.company_id != company_id:
        raise HTTPException(status_code=403, detail="Unauthorized access!")
    return role


def _would_create_cycle(
    db: Session, role_id: int | None, reporting_role_id: int | None
) -> bool:
    if reporting_role_id is None:
        return False
    if role_id is not None and reporting_role_id == role_id:
        return True
    seen = set()
    current_id = reporting_role_id
    while current_id is not None:
        if role_id is not None and current_id == role_id:
            return True
        if current_id in seen:
            return True
        seen.add(current_id)
        parent = (
            db.query(Role)
            .filter(Role.id == current_id, Role.is_delete.is_(False))
            .first()
        )
        if not parent:
            break
        current_id = parent.reporting_role_id
    return False


def _validate_reporting_role(
    db: Session,
    *,
    scope: RoleScope,
    company_id: int | None,
    reporting_role_id: int | None,
    role_id: int | None = None,
) -> Role | None:
    if reporting_role_id is None:
        return None
    parent = _get_role_in_scope(db, reporting_role_id, scope, company_id)
    if _would_create_cycle(db, role_id, reporting_role_id):
        raise HTTPException(
            status_code=400,
            detail="Circular reporting role dependency is not allowed",
        )
    return parent


def _normalize_write_permissions(db: Session, permissions: list, scope: RoleScope) -> list[dict]:
    """Accept only selected modules; identify by menu_key or sidebar_menu_id."""
    allowed_ids = {m.id for m in menus_for_scope(db, scope)}
    normalized = []
    seen = set()
    for item in permissions or []:
        selected = bool(
            getattr(item, "selected", True)
            if not isinstance(item, dict)
            else item.get("selected", True)
        )
        if not selected:
            continue

        sidebar_menu_id = (
            getattr(item, "sidebar_menu_id", None)
            if not isinstance(item, dict)
            else item.get("sidebar_menu_id")
        )
        menu_key = (
            getattr(item, "menu_key", None)
            if not isinstance(item, dict)
            else item.get("menu_key")
        )

        menu = None
        if sidebar_menu_id is not None:
            menu = resolve_menu_by_id(db, int(sidebar_menu_id))
        elif menu_key is not None:
            menu = resolve_menu_by_menu_key(db, int(menu_key))

        if not menu:
            raise HTTPException(status_code=400, detail="Invalid menu_key / sidebar_menu_id")
        if menu.id not in allowed_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Menu not allowed for this scope: {menu.label}",
            )
        if menu.id in seen:
            continue
        seen.add(menu.id)

        add = bool(
            item.add if not isinstance(item, dict) else item.get("add", False)
        )
        edit = bool(
            item.edit if not isinstance(item, dict) else item.get("edit", False)
        )
        delete = bool(
            item.delete if not isinstance(item, dict) else item.get("delete", False)
        )
        normalized.append(
            {
                "sidebar_menu_id": menu.id,
                "view": True,
                "add": add,
                "edit": edit,
                "delete": delete,
            }
        )
    return normalized


def _replace_permissions(db: Session, role: Role, permissions: list[dict]) -> None:
    db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
    for perm in permissions:
        db.add(
            RolePermission(
                role_id=role.id,
                sidebar_menu_id=perm["sidebar_menu_id"],
                view=perm["view"],
                add=perm["add"],
                edit=perm["edit"],
                delete=perm["delete"],
            )
        )
    db.flush()


def _reporting_payload(role: Role | None) -> dict | None:
    if not role:
        return None
    return {"id": role.id, "name": role.name}


def _list_permission_tags(role: Role) -> list[dict]:
    tags = []
    for perm in role.permissions or []:
        if not perm.view:
            continue
        menu = perm.menu
        if not menu:
            continue
        tags.append({"menu_key": menu.menu_key, "label": menu.label})
    return tags


def _detail_permissions(
    db: Session, role: Role, scope: RoleScope, *, selected_only: bool = False
) -> list[dict]:
    stored = {p.sidebar_menu_id: p for p in (role.permissions or [])}
    catalog = menus_for_scope(db, scope)
    result = []
    for menu in catalog:
        perm = stored.get(menu.id)
        selected = bool(perm and perm.view)
        if selected_only and not selected:
            continue
        result.append(
            {
                "menu_key": menu.menu_key,
                "sidebar_menu_id": menu.id,
                "label": menu.label,
                "path": menu.path,
                "selected": selected,
                "view": selected,
                "add": bool(perm.add) if perm else False,
                "edit": bool(perm.edit) if perm else False,
                "delete": bool(perm.delete) if perm else False,
            }
        )
    return result


def _role_detail(
    db: Session, role: Role, scope: RoleScope, *, selected_only: bool = False
) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "is_editable": role.is_editable,
        "reporting_role_id": role.reporting_role_id,
        "reporting_role": _reporting_payload(role.reporting_role),
        "permissions": _detail_permissions(
            db, role, scope, selected_only=selected_only
        ),
    }


def list_roles_service(payload, db: Session, current_user: dict):
    scope = resolve_caller_scope(current_user)
    company_id = current_user.get("company_id")
    page = max(int(getattr(payload, "page", 1) or 1), 1)
    pagelimit = max(int(getattr(payload, "pagelimit", 10) or 10), 1)
    search = (getattr(payload, "search", None) or "").strip()

    query = db.query(Role).options(*_role_load_options())
    query = _scope_filter(query, scope, company_id)
    query = query.filter(Role.is_hidden_from_list.is_(False))

    if search:
        query = query.filter(Role.name.ilike(f"%{search}%"))

    total = query.count()
    roles = (
        query.order_by(Role.id.asc())
        .offset((page - 1) * pagelimit)
        .limit(pagelimit)
        .all()
    )

    data = [
        {
            "id": role.id,
            "name": role.name,
            "is_editable": role.is_editable,
            "reporting_role": _reporting_payload(role.reporting_role),
            "permissions": _list_permission_tags(role),
        }
        for role in roles
    ]
    return {
        "statusCode": 200,
        "message": "Roles fetched successfully",
        "page": page,
        "pagelimit": pagelimit,
        "total": total,
        "data": data,
    }


def list_modules_service(db: Session, current_user: dict):
    scope = resolve_caller_scope(current_user)
    return {
        "statusCode": 200,
        "message": "Modules fetched successfully",
        "data": [menu_payload(m) for m in menus_for_scope(db, scope)],
    }


def reporting_options_service(db: Session, current_user: dict):
    scope = resolve_caller_scope(current_user)
    company_id = current_user.get("company_id")
    query = db.query(Role)
    query = _scope_filter(query, scope, company_id)
    roles = query.order_by(Role.id.asc()).all()
    return {
        "statusCode": 200,
        "message": "Reporting roles fetched successfully",
        "data": [{"id": r.id, "name": r.name} for r in roles],
    }


def get_role_service(role_id: int, db: Session, current_user: dict):
    scope = resolve_caller_scope(current_user)
    role = _get_role_in_scope(db, role_id, scope, current_user.get("company_id"))
    return {
        "statusCode": 200,
        "message": "Role fetched successfully",
        "data": _role_detail(db, role, scope, selected_only=False),
    }


def create_role_service(payload, db: Session, current_user: dict):
    scope = resolve_caller_scope(current_user)
    company_id = current_user.get("company_id") if scope == RoleScope.COMPANY else None
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Role name is required")

    dup_query = db.query(Role).filter(
        Role.name.ilike(name),
        Role.scope == scope,
        Role.is_delete.is_(False),
    )
    if scope == RoleScope.COMPANY:
        dup_query = dup_query.filter(Role.company_id == company_id)
    else:
        dup_query = dup_query.filter(Role.company_id.is_(None))
    if dup_query.first():
        raise HTTPException(status_code=400, detail="Role name already exists")

    parent = _validate_reporting_role(
        db,
        scope=scope,
        company_id=company_id,
        reporting_role_id=payload.reporting_role_id,
    )
    permissions = _normalize_write_permissions(db, payload.permissions, scope)

    try:
        role = Role(
            company_id=company_id,
            scope=scope,
            name=name,
            template_key=None,
            reporting_role_id=parent.id if parent else None,
            is_editable=True,
            is_hidden_from_list=False,
            is_delete=False,
        )
        db.add(role)
        db.flush()
        _replace_permissions(db, role, permissions)
        db.commit()
        role = _get_role_in_scope(db, role.id, scope, company_id)
        return {
            "statusCode": 200,
            "message": "Role created successfully",
            "data": _role_detail(db, role, scope, selected_only=True),
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create role")


def update_role_service(role_id: int, payload, db: Session, current_user: dict):
    scope = resolve_caller_scope(current_user)
    company_id = current_user.get("company_id") if scope == RoleScope.COMPANY else None
    role = _get_role_in_scope(db, role_id, scope, company_id)

    if not role.is_editable:
        raise HTTPException(status_code=400, detail="This role is not editable")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Role name is required")

    dup_query = db.query(Role).filter(
        Role.name.ilike(name),
        Role.scope == scope,
        Role.is_delete.is_(False),
        Role.id != role.id,
    )
    if scope == RoleScope.COMPANY:
        dup_query = dup_query.filter(Role.company_id == company_id)
    else:
        dup_query = dup_query.filter(Role.company_id.is_(None))
    if dup_query.first():
        raise HTTPException(status_code=400, detail="Role name already exists")

    parent = _validate_reporting_role(
        db,
        scope=scope,
        company_id=company_id,
        reporting_role_id=payload.reporting_role_id,
        role_id=role.id,
    )
    permissions = _normalize_write_permissions(db, payload.permissions, scope)

    try:
        role.name = name
        role.reporting_role_id = parent.id if parent else None
        role.updated_at = datetime.utcnow()
        _replace_permissions(db, role, permissions)
        db.commit()
        role = _get_role_in_scope(db, role.id, scope, company_id)
        return {
            "statusCode": 200,
            "message": "Role updated successfully",
            "data": _role_detail(db, role, scope, selected_only=True),
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update role")


def delete_role_service(role_id: int, db: Session, current_user: dict):
    scope = resolve_caller_scope(current_user)
    company_id = current_user.get("company_id") if scope == RoleScope.COMPANY else None
    role = _get_role_in_scope(db, role_id, scope, company_id)

    if not role.is_editable:
        raise HTTPException(status_code=400, detail="This role is not editable")

    assigned = (
        db.query(User)
        .filter(User.role_id == role.id, User.is_delete.is_(False))
        .count()
    )
    if assigned:
        raise HTTPException(
            status_code=400,
            detail="Role cannot be deleted while users are assigned",
        )

    dependents = (
        db.query(Role)
        .filter(Role.reporting_role_id == role.id, Role.is_delete.is_(False))
        .count()
    )
    if dependents:
        raise HTTPException(
            status_code=400,
            detail="Role cannot be deleted while other roles report to it",
        )

    try:
        role.is_delete = True
        role.updated_at = datetime.utcnow()
        db.commit()
        return {"statusCode": 200, "message": "Role deleted successfully"}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete role")


def get_role_by_id(db: Session, role_id: int) -> Role | None:
    return (
        db.query(Role)
        .filter(Role.id == role_id, Role.is_delete.is_(False))
        .first()
    )


def get_company_admin_role(db: Session, company_id: int) -> Role | None:
    return (
        db.query(Role)
        .filter(
            Role.company_id == company_id,
            Role.template_key == "COMPANY_ADMIN",
            Role.is_delete.is_(False),
        )
        .first()
    )


def is_company_admin_role(role: Role | None) -> bool:
    if not role:
        return False
    return role.template_key == "COMPANY_ADMIN"


def validate_assignable_role(
    db: Session,
    *,
    role_id: int,
    company_id: int | None,
    user_type: UserType,
) -> Role:
    role = get_role_by_id(db, role_id)
    if not role:
        raise HTTPException(status_code=400, detail="Invalid role_id")

    if user_type == UserType.SYSTEM:
        if role.scope != RoleScope.SYSTEM or role.company_id is not None:
            raise HTTPException(status_code=400, detail="Invalid system role")
        if role.template_key == "SYSTEM_ADMIN":
            raise HTTPException(
                status_code=400,
                detail="Cannot assign System Admin via employee APIs",
            )
    else:
        if role.scope != RoleScope.COMPANY or role.company_id != company_id:
            raise HTTPException(status_code=400, detail="Invalid company role")
        if role.template_key == "COMPANY_ADMIN":
            raise HTTPException(
                status_code=400,
                detail="Company Admin is assigned only via company contact update",
            )
    return role


def role_summary(role: Role | None) -> dict | None:
    if not role:
        return None
    return {
        "id": role.id,
        "name": role.name,
        "is_editable": role.is_editable,
    }


def walk_reporting_role_chain(db: Session, start_role: Role | None) -> list[Role]:
    """Return reporting roles above start_role (nearest parent first)."""
    chain: list[Role] = []
    seen = set()
    current = start_role
    while current and current.reporting_role_id:
        if current.reporting_role_id in seen:
            break
        seen.add(current.reporting_role_id)
        parent = get_role_by_id(db, current.reporting_role_id)
        if not parent:
            break
        chain.append(parent)
        current = parent
    return chain


def sync_legacy_user_role(user: User, role: Role) -> None:
    user.role = legacy_role_from_template_key(role.template_key)
    user.role_id = role.id


def get_sidebar_for_role_id(db: Session, role_id: int | None) -> list[dict]:
    """Sidebar from role_permissions.view on sidebar_menus (not enum mappings)."""
    if not role_id:
        return []
    menus = (
        db.query(SidebarMenu)
        .join(RolePermission, RolePermission.sidebar_menu_id == SidebarMenu.id)
        .filter(
            RolePermission.role_id == role_id,
            RolePermission.view.is_(True),
            SidebarMenu.is_active.is_(True),
        )
        .order_by(SidebarMenu.sort_order.asc(), SidebarMenu.menu_key.asc())
        .all()
    )
    return [
        {
            "id": m.menu_key,
            "label": m.label,
            "path": m.path,
            "icon": m.icon,
            "icon_active": m.icon_active,
        }
        for m in menus
    ]
