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
