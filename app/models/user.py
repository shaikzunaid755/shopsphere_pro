from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.utils import utcnow


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(String(255))

    permissions = relationship("RolePermission", back_populates="role", cascade="all, delete-orphan")


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(100), unique=True, nullable=False)
    description = Column(String(255))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_perm"),)

    id = Column(Integer, primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission_id = Column(Integer, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)

    role = relationship("Role", back_populates="permissions")
    permission = relationship("Permission")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(120), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    phone = Column(String(15), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)

    # active / inactive / suspended
    status = Column(String(20), default="active", nullable=False)
    is_email_verified = Column(Boolean, default=False)
    # pending -> verified (only used for customers)
    registration_status = Column(String(20), default="pending")
    deactivation_requested = Column(Boolean, default=False)
    deactivation_reason = Column(String(255))

    last_login_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    role = relationship("Role")
    addresses = relationship("Address", back_populates="user")

    @property
    def role_name(self):
        return self.role.name if self.role else None


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jti = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)


class RevokedToken(Base):
    # access tokens that were logged out before they expired
    __tablename__ = "revoked_tokens"

    id = Column(Integer, primary_key=True)
    jti = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)


class UserActivity(Base):
    __tablename__ = "user_activities"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action = Column(String(100), nullable=False)
    details = Column(String(255))
    ip_address = Column(String(50))
    created_at = Column(DateTime, default=utcnow)


class ProfileChangeHistory(Base):
    __tablename__ = "profile_change_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    field_name = Column(String(50))
    old_value = Column(String(255))
    new_value = Column(String(255))
    changed_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)


class Address(Base):
    __tablename__ = "addresses"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    full_name = Column(String(120), nullable=False)
    phone = Column(String(15), nullable=False)
    line1 = Column(String(255), nullable=False)
    line2 = Column(String(255))
    city = Column(String(80), nullable=False)
    state = Column(String(80), nullable=False)
    postal_code = Column(String(10), nullable=False)
    country = Column(String(60), default="India")
    address_type = Column(String(20), default="shipping")  # shipping / billing
    is_default = Column(Boolean, default=False)
    # soft delete, because old orders still point to the address
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="addresses")

    def as_text(self):
        parts = [self.full_name, self.phone, self.line1, self.line2, self.city,
                 self.state, self.postal_code, self.country]
        return ", ".join(p for p in parts if p)