from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List, Literal
from datetime import datetime
from uuid import UUID


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
    contact_number: str = Field(..., max_length=10)
    total_space: int = Field(..., gt=0, description="Total storage space in gb")


class CompanyResponseSchema(BaseModel):
    id: int
    name: str
    contact_person: str
    contact_email: EmailStr
    is_active: bool
    created_at: datetime
    is_delete: bool


class CreateUserSchema(BaseModel):
    name: str = Field(..., max_length=255)
    email: EmailStr

    total_space: int
    remaining_space: int

    role_id: int
    department_id: Optional[int] = None
    reports_to: Optional[int] = None

    class Config:
        from_attributes = True


class UpdateCompanySchema(BaseModel):
    name: str
    contact_person: str
    contact_email: EmailStr
    contact_number: int
    total_space: int
    is_active: bool


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
    contact_person: str = Field(..., max_length=255)
    contact_email: EmailStr
    contact: str = Field(..., max_length=10)


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
    is_active: bool


class CreateDepartmentSchema(BaseModel):
    name: str
    description: str | None = None
    head_user_id: int | None = None
    is_active: bool


class UpdateDeptSchema(BaseModel):
    name: str | None = None
    description: str | None = None
    head_user_id: int | None = None
    is_active: bool


class UpdateDeptStatusSchema(BaseModel):
    is_active: bool


class CreateEmployeeSchema(BaseModel):
    name: str = Field(..., max_length=255)
    email: EmailStr
    department_id: Optional[int] = None
    designation: str = Field(..., max_length=255)
    reports_to: Optional[int] = None
    is_active:bool


class UpdateEmployeeSchema(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    department_id: Optional[int] = None
    reports_to: Optional[int] = None
    designation: Optional[str]
    is_active:bool


class UpdateEmployeeStatusSchema(BaseModel):
    is_active: bool


# ------------------------ AI schemas ------------------------
# DOCUMENT UPLOAD RESPONSE

class UploadAIResponse(BaseModel):
    document_id: int
    chunks: int
    message: str

# CHAT (DOCUMENT-CENTRIC)

class ChatRequest(BaseModel):
    document_id: int = Field(..., gt=0)
    query: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    document_id: int
    session_id: str
    answer: str
    citations: List[dict]

class SummaryResponse(BaseModel):
    document_id: int
    summary: str
    tags: List[str]
    citations: List[dict]

    model_config = ConfigDict(from_attributes=True)

class DocumentSaveSchema(BaseModel):
    summary: Optional[str] = None
    tags: Optional[List[str]] = None

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)


class AskTextPayload(BaseModel):
    text: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    mode: str = Field(default="documents")
   
class GetCompaniesRequest(BaseModel):

    page: int = 1
    pagelimit: int = 10
    search: Optional[str] = None
    status: Optional[str] = None


class getDepartments(BaseModel):
    page: int = 1
    pagelimit: int = 10
    search: Optional[str] = None
    status: Optional[str] = None 

class getDepartmentList(BaseModel):
    search:Optional[str]=None

class DocumentAssignSchema(BaseModel):
    assignee_ids: list[int]


class GetEmployee(BaseModel):
    page: int = 1
    pagelimit: int = 10
    search: Optional[str] = None
    status:Optional[str]=None

class GetEmployeeList(BaseModel):
    search:Optional[str]=None