"""Seed role templates, system/company roles, and sidebar-backed permissions."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.enum import RoleScope, UserRole, UserType
from app.models import Role, RolePermission, RoleTemplate, SidebarMenu, User
from app.role_modules import (
    COMPANY_ADMIN_PERMS,
    DEPARTMENT_HEAD_PERMS,
    EMPLOYEE_PERMS,
    LEGACY_MODULE_TO_PATH,
    PATH_SCOPE,
    SYSTEM_ADMIN_PERMS,
    menus_for_scope,
    resolve_menu_by_path,
)


TEMPLATE_DEFS = [
    {
        "template_key": "SYSTEM_ADMIN",
        "name": "System Admin",
        "scope": RoleScope.SYSTEM,
        "is_editable": False,
        "is_hidden_from_list": True,
        "sort_order": 0,
        "reporting_template_key": None,
        "default_permissions": SYSTEM_ADMIN_PERMS,
    },
    {
        "template_key": "COMPANY_ADMIN",
        "name": "Company Admin",
        "scope": RoleScope.COMPANY,
        "is_editable": False,
        "is_hidden_from_list": True,
        "sort_order": 0,
        "reporting_template_key": None,
        "default_permissions": COMPANY_ADMIN_PERMS,
    },
    {
        "template_key": "DEPARTMENT_HEAD",
        "name": "Department Head",
        "scope": RoleScope.COMPANY,
        "is_editable": True,
        "is_hidden_from_list": False,
        "sort_order": 1,
        "reporting_template_key": "COMPANY_ADMIN",
        "default_permissions": DEPARTMENT_HEAD_PERMS,
    },
    {
        "template_key": "EMPLOYEE",
        "name": "Employee",
        "scope": RoleScope.COMPANY,
        "is_editable": True,
        "is_hidden_from_list": False,
        "sort_order": 2,
        "reporting_template_key": "DEPARTMENT_HEAD",
        "default_permissions": EMPLOYEE_PERMS,
    },
]

TEMPLATE_KEY_TO_USER_ROLE = {
    "SYSTEM_ADMIN": UserRole.SYSTEM_ADMIN,
    "COMPANY_ADMIN": UserRole.COMPANY_ADMIN,
    "DEPARTMENT_HEAD": UserRole.DEPARTMENT_HEAD,
    "EMPLOYEE": UserRole.EMPLOYEE,
}

TEMPLATE_PERMS = {
    "SYSTEM_ADMIN": SYSTEM_ADMIN_PERMS,
    "COMPANY_ADMIN": COMPANY_ADMIN_PERMS,
    "DEPARTMENT_HEAD": DEPARTMENT_HEAD_PERMS,
    "EMPLOYEE": EMPLOYEE_PERMS,
}


def backfill_sidebar_menu_scopes(db: Session) -> None:
    menus = db.query(SidebarMenu).all()
    for menu in menus:
        path = (menu.path or "").strip()
        scope = PATH_SCOPE.get(path, "COMPANY")
        if not getattr(menu, "scope", None) or menu.scope not in (
            "SYSTEM",
            "COMPANY",
            "BOTH",
        ):
            menu.scope = scope
        # Force known system/both paths even if previously wrong
        if path in PATH_SCOPE:
            menu.scope = PATH_SCOPE[path]
    db.flush()


def seed_role_templates(db: Session) -> None:
    existing = {t.template_key: t for t in db.query(RoleTemplate).all()}
    for definition in TEMPLATE_DEFS:
        row = existing.get(definition["template_key"])
        if row:
            # Keep template permission defs in sync with path-based defaults
            row.default_permissions = definition["default_permissions"]
            continue
        db.add(RoleTemplate(**definition))
    db.flush()


def _permission_dicts_from_template(db: Session, permissions: list) -> list[dict]:
    """Resolve template permission entries (path or legacy module_key) to menu ids."""
    resolved = []
    seen = set()
    for perm in permissions or []:
        path = None
        if isinstance(perm, dict):
            path = perm.get("path")
            if not path and perm.get("module_key"):
                path = LEGACY_MODULE_TO_PATH.get(str(perm["module_key"]))
            view = bool(perm.get("view", False))
            add = bool(perm.get("add", False))
            edit = bool(perm.get("edit", False))
            delete = bool(perm.get("delete", False))
        else:
            continue
        if not path:
            continue
        menu = resolve_menu_by_path(db, path)
        if not menu or menu.id in seen:
            continue
        seen.add(menu.id)
        resolved.append(
            {
                "sidebar_menu_id": menu.id,
                "view": view,
                "add": add,
                "edit": edit,
                "delete": delete,
            }
        )
    return resolved


def _apply_permissions(db: Session, role: Role, permissions: list[dict]) -> None:
    db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
    for perm in permissions:
        db.add(
            RolePermission(
                role_id=role.id,
                sidebar_menu_id=perm["sidebar_menu_id"],
                view=bool(perm.get("view", False)),
                add=bool(perm.get("add", False)),
                edit=bool(perm.get("edit", False)),
                delete=bool(perm.get("delete", False)),
            )
        )
    db.flush()


def apply_template_permissions(db: Session, role: Role, template_key: str | None) -> None:
    raw = TEMPLATE_PERMS.get(template_key or "", [])
    if not raw and template_key:
        template = (
            db.query(RoleTemplate)
            .filter(RoleTemplate.template_key == template_key)
            .first()
        )
        raw = (template.default_permissions if template else []) or []
    resolved = _permission_dicts_from_template(db, raw)
    # System/Company Admin: grant full access to every menu in scope
    if template_key in ("SYSTEM_ADMIN", "COMPANY_ADMIN"):
        scope = RoleScope.SYSTEM if template_key == "SYSTEM_ADMIN" else RoleScope.COMPANY
        menus = menus_for_scope(db, scope)
        by_id = {p["sidebar_menu_id"]: p for p in resolved}
        for menu in menus:
            if menu.id not in by_id:
                by_id[menu.id] = {
                    "sidebar_menu_id": menu.id,
                    "view": True,
                    "add": True,
                    "edit": True,
                    "delete": True,
                }
            else:
                by_id[menu.id].update(
                    {"view": True, "add": True, "edit": True, "delete": True}
                )
        resolved = list(by_id.values())
    _apply_permissions(db, role, resolved)


def ensure_system_admin_role(db: Session) -> Role:
    role = (
        db.query(Role)
        .filter(
            Role.scope == RoleScope.SYSTEM,
            Role.template_key == "SYSTEM_ADMIN",
            Role.company_id.is_(None),
            Role.is_delete.is_(False),
        )
        .first()
    )
    if not role:
        template = (
            db.query(RoleTemplate)
            .filter(RoleTemplate.template_key == "SYSTEM_ADMIN")
            .first()
        )
        role = Role(
            company_id=None,
            scope=RoleScope.SYSTEM,
            name=template.name if template else "System Admin",
            template_key="SYSTEM_ADMIN",
            reporting_role_id=None,
            is_editable=False,
            is_hidden_from_list=True,
            is_delete=False,
        )
        db.add(role)
        db.flush()

    apply_template_permissions(db, role, "SYSTEM_ADMIN")
    return role


def seed_company_roles(db: Session, company_id: int) -> dict[str, Role]:
    """Clone company templates into live roles for a company. Returns key -> Role."""
    existing = (
        db.query(Role)
        .filter(
            Role.company_id == company_id,
            Role.scope == RoleScope.COMPANY,
            Role.is_delete.is_(False),
        )
        .all()
    )
    if existing:
        by_key = {r.template_key: r for r in existing if r.template_key}
        for key, role in by_key.items():
            if key not in TEMPLATE_PERMS:
                continue
            perm_count = (
                db.query(RolePermission)
                .filter(RolePermission.role_id == role.id)
                .count()
            )
            # Non-editable seeded admins stay in sync with sidebar_menus;
            # editable roles are only filled when empty (don't overwrite customizations).
            if not role.is_editable or perm_count == 0:
                apply_template_permissions(db, role, key)
        return by_key

    templates = (
        db.query(RoleTemplate)
        .filter(RoleTemplate.scope == RoleScope.COMPANY)
        .order_by(RoleTemplate.sort_order.asc())
        .all()
    )
    created: dict[str, Role] = {}
    for template in templates:
        role = Role(
            company_id=company_id,
            scope=RoleScope.COMPANY,
            name=template.name,
            template_key=template.template_key,
            reporting_role_id=None,
            is_editable=template.is_editable,
            is_hidden_from_list=template.is_hidden_from_list,
            is_delete=False,
        )
        db.add(role)
        db.flush()
        apply_template_permissions(db, role, template.template_key)
        created[template.template_key] = role

    for template in templates:
        if not template.reporting_template_key:
            continue
        child = created.get(template.template_key)
        parent = created.get(template.reporting_template_key)
        if child and parent:
            child.reporting_role_id = parent.id

    db.flush()
    return created


def migrate_role_permissions_module_keys(db: Session) -> None:
    """Convert legacy module_key rows to sidebar_menu_id (idempotent)."""
    # Detect whether module_key column still exists
    cols = {
        row[0]
        for row in db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'role_permissions'"
            )
        )
    }
    if "module_key" not in cols or "sidebar_menu_id" not in cols:
        return

    rows = db.execute(
        text(
            'SELECT id, role_id, module_key, view, "add", edit, delete '
            "FROM role_permissions WHERE sidebar_menu_id IS NULL"
        )
    ).fetchall()
    for row in rows:
        path = LEGACY_MODULE_TO_PATH.get(row.module_key)
        if not path:
            db.execute(
                text("DELETE FROM role_permissions WHERE id = :id"),
                {"id": row.id},
            )
            continue
        menu = resolve_menu_by_path(db, path)
        if not menu:
            db.execute(
                text("DELETE FROM role_permissions WHERE id = :id"),
                {"id": row.id},
            )
            continue
        # Avoid unique conflicts
        exists = db.execute(
            text(
                "SELECT id FROM role_permissions "
                "WHERE role_id = :role_id AND sidebar_menu_id = :menu_id"
            ),
            {"role_id": row.role_id, "menu_id": menu.id},
        ).first()
        if exists:
            db.execute(
                text("DELETE FROM role_permissions WHERE id = :id"),
                {"id": row.id},
            )
        else:
            db.execute(
                text(
                    "UPDATE role_permissions SET sidebar_menu_id = :menu_id "
                    "WHERE id = :id"
                ),
                {"menu_id": menu.id, "id": row.id},
            )
    db.flush()


def backfill_user_role_links(db: Session) -> None:
    """Link existing users to roles when role_id is missing."""
    system_admin_role = ensure_system_admin_role(db)

    system_users = (
        db.query(User)
        .filter(
            User.role == UserRole.SYSTEM_ADMIN,
            User.is_delete.is_(False),
            User.role_id.is_(None),
        )
        .all()
    )
    for user in system_users:
        user.role_id = system_admin_role.id
        user.user_type = UserType.SYSTEM

    # Ensure linked system admins keep System Admin role_id
    for user in (
        db.query(User)
        .filter(User.role == UserRole.SYSTEM_ADMIN, User.is_delete.is_(False))
        .all()
    ):
        user.role_id = system_admin_role.id
        user.user_type = UserType.SYSTEM

    company_ids = {
        row[0]
        for row in db.query(User.company_id)
        .filter(User.company_id.isnot(None), User.is_delete.is_(False))
        .distinct()
        .all()
    }
    for company_id in company_ids:
        roles = seed_company_roles(db, company_id)
        users = (
            db.query(User)
            .filter(User.company_id == company_id, User.is_delete.is_(False))
            .all()
        )
        for user in users:
            user.user_type = UserType.COMPANY
            if user.role_id:
                continue
            key = user.role.value if hasattr(user.role, "value") else str(user.role)
            mapped = roles.get(key)
            if mapped:
                user.role_id = mapped.id
            elif roles.get("EMPLOYEE"):
                user.role_id = roles["EMPLOYEE"].id


def finalize_role_permissions_schema(db: Session) -> None:
    """Drop legacy module_key and enforce sidebar_menu_id after migration."""
    cols = {
        row[0]
        for row in db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'role_permissions'"
            )
        )
    }
    if "sidebar_menu_id" not in cols:
        return

    db.execute(text("DELETE FROM role_permissions WHERE sidebar_menu_id IS NULL"))
    db.execute(
        text(
            "DO $$ BEGIN "
            "ALTER TABLE role_permissions "
            "ALTER COLUMN sidebar_menu_id SET NOT NULL; "
            "EXCEPTION WHEN others THEN NULL; END $$;"
        )
    )
    if "module_key" in cols:
        db.execute(text("ALTER TABLE role_permissions DROP COLUMN IF EXISTS module_key"))
    db.execute(text("ALTER TABLE role_permissions DROP CONSTRAINT IF EXISTS uq_role_module"))
    db.execute(
        text(
            "DO $$ BEGIN "
            "ALTER TABLE role_permissions "
            "ADD CONSTRAINT uq_role_sidebar_menu UNIQUE (role_id, sidebar_menu_id); "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "WHEN duplicate_table THEN NULL; END $$;"
        )
    )
    db.flush()


def bootstrap_roles(db: Session) -> None:
    backfill_sidebar_menu_scopes(db)
    seed_role_templates(db)
    migrate_role_permissions_module_keys(db)
    ensure_system_admin_role(db)
    backfill_user_role_links(db)
    finalize_role_permissions_schema(db)
    db.commit()


def legacy_role_from_template_key(template_key: str | None) -> UserRole:
    if template_key and template_key in TEMPLATE_KEY_TO_USER_ROLE:
        return TEMPLATE_KEY_TO_USER_ROLE[template_key]
    return UserRole.EMPLOYEE
