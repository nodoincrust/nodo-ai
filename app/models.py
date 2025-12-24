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
)
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base

from sqlalchemy import Column, BigInteger, String, DateTime, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base
from app.enum import UserRole

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)

    company_id = Column(
        BigInteger,
        ForeignKey("companies.id"),
        nullable=True,   # SYSTEM_ADMIN allowed
    )

    role = Column(Enum(UserRole, name="user_role_enum"), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)


    department_id = Column(BigInteger, ForeignKey("departments.id"), nullable=True)
    reports_to = Column(BigInteger, ForeignKey("users.id"), nullable=True)

    name = Column(String(255))
    email = Column(String(255), unique=True, nullable=False)

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
    contact_email = Column(String(255), unique=True, nullable=False)

    created_by = Column(
        BigInteger,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    is_active = Column(Boolean, default=True, nullable=False, index=True)
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

    id = Column(BigInteger, primary_key=True)
    company_id = Column(BigInteger, ForeignKey("companies.id"), nullable=False)
    name = Column(String(255), nullable=False)
    reporting_department_id = Column(
        BigInteger, ForeignKey("departments.id"), nullable=True
    )
    created_at = Column(DateTime, default=datetime.utcnow)
