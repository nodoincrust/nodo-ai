from enum import Enum


class UserRole(str, Enum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    COMPANY_ADMIN = "COMPANY_ADMIN"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD"
    MANAGER = "MANAGER"
    USER = "USER"



class DocumentStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"



SIDEBAR_MENU = {
    UserRole.SYSTEM_ADMIN: [
        {"key": "dashboard", "label": "Dashboard", "path": "/dashboard"},
        {"key": "companies", "label": "Companies", "path": "/companies"},
    ],

    UserRole.COMPANY_ADMIN: [
        {"key": "dashboard", "label": "Dashboard", "path": "/dashboard"},
        {"key": "departments", "label": "Departments", "path": "/departments"},
        {"key": "users", "label": "Users", "path": "/users"},
    ],

    UserRole.DEPARTMENT_HEAD: [
        {"key": "dashboard", "label": "Dashboard", "path": "/dashboard"},
        {"key": "dept_docs", "label": "Department Documents", "path": "/documents/department"},
        {"key": "approvals", "label": "Approvals", "path": "/approvals"},
        {"key": "users", "label": "Department Users", "path": "/users/department"},
    ],

    UserRole.MANAGER: [
        {"key": "dashboard", "label": "Dashboard", "path": "/dashboard"},
        {"key": "my_docs", "label": "My Documents", "path": "/documents/my"},
        {"key": "approvals", "label": "Approvals", "path": "/approvals"},
    ],

    UserRole.USER: [
        {"key": "dashboard", "label": "Dashboard", "path": "/dashboard"},
        {"key": "my_docs", "label": "My Documents", "path": "/documents/my"},
    ],
}