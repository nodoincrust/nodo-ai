import uuid
from sqlalchemy import (
    Column,
    BigInteger,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Enum,
    Text,
    Integer,
    Numeric,
    CheckConstraint,
    UniqueConstraint,
    JSON,
    
)
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from sqlalchemy.sql import func
from app.db import Base
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from pgvector.sqlalchemy import Vector
from app.db import Base
from app.enum import UserRole


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)

    company_id = Column(
        BigInteger,
        ForeignKey("companies.id"),
        nullable=True,
    )

    role = Column(Enum(UserRole, name="user_role_enum"), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_delete = Column(Boolean, default=False, nullable=False, index=True)

    department_id = Column(BigInteger, ForeignKey("departments.id"), nullable=True)
    reports_to = Column(BigInteger, ForeignKey("users.id"), nullable=True)

    name = Column(String(255))
    email = Column(String(255), unique=True, nullable=False)
    designation = Column(String(255))

    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship(
        "Company",
        back_populates="users",
        foreign_keys=[company_id],
    )

    manager = relationship("User", remote_side=[id])

    created_companies = relationship(
        "Company",
        foreign_keys="Company.created_by",
        back_populates="creator",
    )


class Company(Base):
    __tablename__ = "companies"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    contact_person = Column(String(255))
    contact_email = Column(String(255), unique=False, nullable=False)
    contact_number = Column(String(10), unique=True, nullable=False)

    created_by = Column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    total_space = Column(BigInteger, default=0, nullable=False)
    remaining_space = Column(BigInteger, default=0, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_delete = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship(
        "User",
        back_populates="company",
        foreign_keys="User.company_id",
        cascade="all, delete",
    )

    creator = relationship(
        "User",
        foreign_keys=[created_by],
        back_populates="created_companies",
    )


class OTPLogin(Base):
    __tablename__ = "login_otps"

    id = Column(BigInteger, primary_key=True, index=True)

    user_id = Column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    otp_code = Column(String(6), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", backref="otp_logins")


class Department(Base):
    __tablename__ = "departments"

    id = Column(BigInteger, primary_key=True, index=True)

    company_id = Column(
        BigInteger, ForeignKey("companies.id"), nullable=False, index=True
    )

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    head_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)

    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_delete = Column(Boolean, default=False, nullable=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    head = relationship("User", foreign_keys=[head_user_id])

    # ------------------------AI MODELS-----------------------------


# from db import Base
EMBEDDING_DIMENSION = 768

# =====================================================
# DOCUMENT ROOT (SINGLE SOURCE OF TRUTH)
# =====================================================


class Document(Base):
    __tablename__ = "documents"

    id = Column(BigInteger, primary_key=True)

    company_id = Column(BigInteger, ForeignKey("companies.id"), nullable=False)
    department_id = Column(BigInteger, ForeignKey("departments.id"), nullable=False)
    uploaded_by = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    current_file_path = Column(Text, nullable=True)

    current_version = Column(Integer, default=1)
    current_step_order = Column(Integer, nullable=True)
    current_assignee_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String, default="DRAFT")
    is_active = Column(Boolean, default=True)
    is_delete = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    ai_document = relationship(
        "AIDocument",
        back_populates="document",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ChatSession(Base):
    __tablename__ = "sessions"

    session_id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_active = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AIDocument(Base):
    __tablename__ = "ai_documents"

    id = Column(BigInteger, primary_key=True)

    document_id = Column(
        BigInteger, ForeignKey("documents.id"), nullable=False, index=True
    )
    version_id=Column(BigInteger,nullable=True)

    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    filename = Column(String(512), nullable=False)
    file_type = Column(String(128))
    file_size_mb = Column(Numeric)

    created_at = Column(DateTime, server_default=func.now())

    document = relationship("Document", back_populates="ai_document")
    chunks = relationship(
        "DocumentChunk",
        back_populates="ai_document",
        cascade="all, delete-orphan",
    )

    summaries = relationship("DocumentSummary", back_populates="ai_document", lazy="dynamic")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    ai_document_id = Column(
        BigInteger,
        ForeignKey("ai_documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )

    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(768))
    page_number = Column(Integer)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    ai_document = relationship("AIDocument", back_populates="chunks")

    __table_args__ = (
        UniqueConstraint(
            "ai_document_id",
            "chunk_index",
            name="uq_ai_document_chunk_index",
        ),
    )
class DocumentSummary(Base):
    __tablename__ = "document_summaries"

    ai_document_id = Column(
        BigInteger,
        ForeignKey("ai_documents.id", ondelete="CASCADE"),
        primary_key=True,
    )

    version_id = Column(
        BigInteger,
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )

    summary_text = Column(Text, nullable=False)
    tags = Column(JSONB)
    citations = Column(JSONB)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    ai_document = relationship("AIDocument", back_populates="summaries")
    is_self_generated=Column(Boolean,default=False)



class SessionMessage(Base):
    __tablename__ = "session_messages"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )

    document_id = Column(
        BigInteger,
        ForeignKey("ai_documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (CheckConstraint("role IN ('user','assistant','system')"),)


class SessionMemorySummary(Base):
    __tablename__ = "session_memory_summaries"

    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        primary_key=True,
    )

    summary = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SidebarMenu(Base):
    __tablename__ = "sidebar_menus"

    id = Column(BigInteger, primary_key=True)
    menu_key = Column(String(50), unique=True, nullable=False)
    label = Column(String(100), nullable=False)
    path = Column(String(255), nullable=False)
    icon = Column(Text)
    icon_active = Column(Text)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)


class RoleSidebarMapping(Base):
    __tablename__ = "role_sidebar_mappings"

    id = Column(BigInteger, primary_key=True)
    role = Column(Enum(UserRole, name="user_role_enum"), nullable=False)
    sidebar_menu_id = Column(
        BigInteger, ForeignKey("sidebar_menus.id", ondelete="CASCADE"), nullable=False
    )

    menu = relationship("SidebarMenu")


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(BigInteger, primary_key=True)
    document_id = Column(BigInteger, ForeignKey("documents.id"), nullable=False)

    version_number = Column(Integer, nullable=False)

    file_path = Column(Text, nullable=False)
    file_name = Column(String(255), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)

    summary = Column(Text, nullable=True)
    tags = Column(JSONB, nullable=True)  # JSON/text for now

    ai_document_id = Column(PG_UUID(as_uuid=True), nullable=True)

    created_by = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    visibility = Column(String(20), default="PRIVATE")
    public_at = Column(DateTime)


class DocumentReview(Base):
    __tablename__ = "document_reviews"

    id = Column(BigInteger, primary_key=True)
    document_id = Column(BigInteger, ForeignKey("documents.id"), nullable=False)
    reviewed_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)

    status = Column(
        Enum("PENDING", "APPROVED", "REJECTED", name="review_status_enum"),
        nullable=False,
    )

    comments = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class DocumentApprovalStep(Base):
    __tablename__ = "document_approval_steps"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)

    step_order = Column(Integer, nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=False)
    approver_type = Column(String, nullable=False)

    status = Column(String, default="PENDING")
    version_id = Column(BigInteger, ForeignKey("document_versions.id"))
    remarks = Column(String)
    action_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class DocumentWorkflowRun(Base):
    __tablename__ = "document_workflow_runs"

    id = Column(BigInteger, primary_key=True)
    document_id = Column(BigInteger, ForeignKey("documents.id"), nullable=False)
    version_id = Column(BigInteger, ForeignKey("document_versions.id"), nullable=False)
    workflow_status = Column(String(20), nullable=False)
    rejected_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    public_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class Bouquet(Base):
    __tablename__ = "bouquets"
 
    id = Column(BigInteger, primary_key=True, index=True)
 
    name = Column(String, nullable=False,unique=True)
    description = Column(Text)
    documentsInBouquet = Column(
        "documents_in_bouquet",
        MutableList.as_mutable(JSONB),
        nullable=False,
        default=list,
    )
 
    isActive = Column("is_active", Boolean, default=True)
    isDelete = Column("is_delete", Boolean, default=False)
 
    createdBy = Column("created_by", Integer, nullable=False)
    updatedAt = Column("updated_at", DateTime)
    

class Designation(Base):
    __tablename__="designation"
    id = Column(BigInteger,primary_key=True,index=True)
    name=Column(String,nullable=False)
    