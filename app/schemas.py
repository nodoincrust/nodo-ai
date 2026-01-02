from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional,List,Literal
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
    contact_number:str=Field(..., max_length=10)
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
    contact_number:int
    total_space:int
    is_active:bool


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


class UpdateDeptSchema(BaseModel):
    name: str | None = None
    description: str | None = None
    head_user_id: int | None = None


class UpdateDeptStatusSchema(BaseModel):
    is_active: bool


class CreateEmployeeSchema(BaseModel):
    name: str = Field(..., max_length=255)
    email: EmailStr
    department_id: Optional[int] = None
    designation: str = Field(..., max_length=255)
    reports_to: Optional[int] = None


class UpdateEmployeeSchema(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    department_id: Optional[int] = None
    reports_to: Optional[int] = None
    designation: Optional[str]


class UpdateEmployeeStatusSchema(BaseModel):
    is_active: bool


# ------------------------ AI schemas ------------------------
class CreateSessionResponse(BaseModel):
    session_id: str


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    query: str


class CitationRequest(BaseModel):
    query: str
    document_id: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    response: str


class AskRequest(BaseModel):
    question: str


class AskTextPayload(BaseModel):
    text: str
    question: str
    mode: str = "docuemnts"  # or "web"


class UploadResponse(BaseModel):
    document_id: str
    chunks: int
    message: str


class AnswerResponse(BaseModel):
    question: str
    answer: str


class SummeryResponse(BaseModel):
    document_id: str
    summary: str

    model_config = ConfigDict(from_attributes=True)
class DocumentSaveSchema(BaseModel):
    summary: Optional[str] = None
    tags: Optional[List[str]] = None
   
class GetCompaniesRequest(BaseModel):
    
    page: int = 1
    pagelimit: int = 10
    search: Optional[str] = None
    

class DocumentAssignSchema(BaseModel):
    assign_level: Literal[ "DEPARTMENT_HEAD", "COMPANY_ADMIN"]