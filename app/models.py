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
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
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


class AIDocument(Base):
    __tablename__ = "ai_documents"

    document_id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.session_id"),
        nullable=True,
    )

    filename = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    file_size_mb = Column(
        Numeric,
        CheckConstraint("file_size_mb >= 0"),
        nullable=True,
    )

    created_time = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    chunks = relationship(
        "DocuementChunks",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    summary = relationship(
        "DocuemntSummery",
        back_populates="document",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Document(Base):
    __tablename__ = "documents"

    id = Column(BigInteger, primary_key=True, index=True)

    company_id = Column(BigInteger, ForeignKey("companies.id"), nullable=False)
    department_id = Column(BigInteger, ForeignKey("departments.id"), nullable=False)
    uploaded_by = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    current_file_path = Column(Text, nullable=True)

    status = Column(
        Enum(
            "DRAFT",
            "SUBMITTED",
            "UNDER_REVIEW",
            "APPROVED",
            "REJECTED",
            name="document_status_enum",
        ),
        nullable=False,
        default="DRAFT",
        index=True,
    )

    current_version = Column(Integer, default=1)
    current_step_order = Column(Integer, nullable=True)
    current_assignee_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String, default="DRAFT")
    is_active = Column(Boolean, default=True)
    is_delete = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)


class DocuementChunks(Base):
    __tablename__ = "Docuement_Chunks"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_documents.document_id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("sessions.session_id"), nullable=True
    )
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIMENSION), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("AIDocument", back_populates="chunks")


class DocuemntSummery(Base):
    __tablename__ = "Document_Summaries"

    document_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    )
    summery_text = Column(Text, nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    document = relationship("AIDocument", back_populates="summary")


class ChatSession(Base):
    __tablename__ = "sessions"
    session_id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_active = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    messages = relationship(
        "SessionMessages", back_populates="session", cascade="all, delete-orphan"
    )
    memorySummery = relationship(
        "SessionMemorySummery",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )


class SessionMessages(Base):
    __tablename__ = "session_messages"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String, nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (CheckConstraint("role IN ('user', 'assistant','system')"),)
    session = relationship("ChatSession", back_populates="messages")


class SessionMemorySummery(Base):
    __tablename__ = "session_memory_summaries"
    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    summary = Column(Text, nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    session = relationship("ChatSession", back_populates="memorySummery")


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

    remarks = Column(String)
    action_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
