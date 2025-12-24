from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


class RequestOTPSchema(BaseModel):
    email: EmailStr

class VerifyOTPSchema(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=4, max_length=4)


class LoginResponseSchema(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MessageSchema(BaseModel):
    message: str


class CreateCompanySchema(BaseModel):
    name: str = Field(..., max_length=255)
    contact_person: str = Field(..., max_length=255)
    contact_email: EmailStr

class CompanyResponseSchema(BaseModel):
    id: int
    name: str
    contact_person: str
    contact_email: EmailStr
    is_active: bool
    created_at: datetime

class CreateUserSchema(BaseModel):
    name: str = Field(..., max_length=255)
    email: EmailStr
    role_id: int
    department_id: Optional[int] = None
    reports_to: Optional[int] = None


class UserResponseSchema(BaseModel):
    id: int
    name: Optional[str]
    email: EmailStr
    role: str
    department_id: Optional[int]
    reports_to: Optional[int]
    is_active: bool
    created_at: datetime


class CreateDepartmentSchema(BaseModel):
    name: str = Field(..., max_length=255)
    reporting_department_id: Optional[int] = None


class DepartmentResponseSchema(BaseModel):
    id: int
    name: str
    reporting_department_id: Optional[int]
    created_at: datetime


class CreateDocumentSchema(BaseModel):
    title: Optional[str]


class UploadDocumentVersionSchema(BaseModel):
    file_name: str
    file_type: str
    file_size: int
    change_reason: Optional[str] = None


class DocumentResponseSchema(BaseModel):
    id: int
    title: Optional[str]
    status: str
    current_version: int
    created_at: datetime


class DocumentVersionResponseSchema(BaseModel):
    version: int
    file_name: str
    s3_bucket: str
    s3_key: str
    uploaded_by: int
    created_at: datetime


class AddCommentSchema(BaseModel):
    comment: str


class ApprovalActionSchema(BaseModel):
    is_active: bool  
    remarks: Optional[str] = None

class UpdateCompanyStatusSchema(BaseModel):
    is_active:bool