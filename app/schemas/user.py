import re
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.roles import ALL_ROLES
from app.schemas.common import ORMModel

PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def check_password(v: str):
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
        raise ValueError("Password must contain letters and numbers")
    return v


def check_phone(v):
    if v is None:
        return v
    v = v.strip().replace(" ", "")
    if v.startswith("+91"):
        v = v[3:]
    if not PHONE_RE.match(v):
        raise ValueError("Enter a valid 10 digit Indian mobile number")
    return v


# ---------- auth ----------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    user_id: int


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    _pw = field_validator("new_password")(check_password)


# ---------- registration ----------
class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str
    password: str

    _pw = field_validator("password")(check_password)
    _ph = field_validator("phone")(check_phone)


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class RegisterResponse(BaseModel):
    message: str
    user_id: int
    registration_status: str
    # only returned when DEBUG=True so you can test without a mail server
    verification_token: Optional[str] = None


# ---------- users ----------
class RoleOut(ORMModel):
    id: int
    name: str
    description: Optional[str] = None


class PermissionOut(ORMModel):
    id: int
    code: str
    description: Optional[str] = None


class RolePermissionsUpdate(BaseModel):
    permission_codes: List[str]


class UserCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: Optional[str] = None
    password: str
    role: str

    _pw = field_validator("password")(check_password)
    _ph = field_validator("phone")(check_phone)

    @field_validator("role")
    @classmethod
    def valid_role(cls, v):
        if v not in ALL_ROLES:
            raise ValueError(f"role must be one of {ALL_ROLES}")
        return v


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    phone: Optional[str] = None

    _ph = field_validator("phone")(check_phone)


class AssignRoleRequest(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def valid_role(cls, v):
        if v not in ALL_ROLES:
            raise ValueError(f"role must be one of {ALL_ROLES}")
        return v


class UserStatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None

    @field_validator("status")
    @classmethod
    def valid_status(cls, v):
        if v not in ("active", "inactive", "suspended"):
            raise ValueError("status must be active, inactive or suspended")
        return v


class UserOut(ORMModel):
    id: int
    full_name: str
    email: str
    phone: Optional[str] = None
    role_name: Optional[str] = None
    status: str
    is_email_verified: bool
    registration_status: Optional[str] = None
    deactivation_requested: bool = False
    last_login_at: Optional[datetime] = None
    created_at: datetime


class ActivityOut(ORMModel):
    id: int
    user_id: int
    action: str
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime


# ---------- profile ----------
class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    phone: Optional[str] = None
    email: Optional[EmailStr] = None

    _ph = field_validator("phone")(check_phone)


class ProfileHistoryOut(ORMModel):
    id: int
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_by: Optional[int] = None
    created_at: datetime


class DeactivationRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


# ---------- addresses ----------
class AddressBase(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str
    line1: str = Field(min_length=3, max_length=255)
    line2: Optional[str] = None
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    postal_code: str
    country: str = "India"
    address_type: str = "shipping"
    is_default: bool = False

    _ph = field_validator("phone")(check_phone)

    @field_validator("postal_code")
    @classmethod
    def pincode(cls, v):
        if not re.match(r"^[1-9]\d{5}$", v):
            raise ValueError("Postal code must be a valid 6 digit pincode")
        return v

    @field_validator("address_type")
    @classmethod
    def addr_type(cls, v):
        if v not in ("shipping", "billing"):
            raise ValueError("address_type must be shipping or billing")
        return v


class AddressCreate(AddressBase):
    pass


class AddressUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    line1: Optional[str] = None
    line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    address_type: Optional[str] = None

    _ph = field_validator("phone")(check_phone)

    @field_validator("postal_code")
    @classmethod
    def pincode(cls, v):
        if v is not None and not re.match(r"^[1-9]\d{5}$", v):
            raise ValueError("Postal code must be a valid 6 digit pincode")
        return v

    @field_validator("address_type")
    @classmethod
    def addr_type(cls, v):
        if v is not None and v not in ("shipping", "billing"):
            raise ValueError("address_type must be shipping or billing")
        return v


class AddressOut(ORMModel):
    id: int
    user_id: int
    full_name: str
    phone: str
    line1: str
    line2: Optional[str] = None
    city: str
    state: str
    postal_code: str
    country: str
    address_type: str
    is_default: bool
    created_at: datetime


    id: int
    name: str
    description: Optional[str] = None


class PermissionOut(ORMModel):
    id: int
    code: str
    description: Optional[str] = None


class RolePermissionsUpdate(BaseModel):
    permission_codes: List[str]


class UserCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: Optional[str] = None
    password: str
    role: str

    _pw = field_validator("password")(check_password)
    _ph = field_validator("phone")(check_phone)

    @field_validator("role")
    @classmethod
    def valid_role(cls, v):
        if v not in ALL_ROLES:
            raise ValueError(f"role must be one of {ALL_ROLES}")
        return v

class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    phone: Optional[str] = None

    _ph = field_validator("phone")(check_phone)


class AssignRoleRequest(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def valid_role(cls, v):
        if v not in ALL_ROLES:
            raise ValueError(f"role must be one of {ALL_ROLES}")
        return v


class UserStatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None

    @field_validator("status")
    @classmethod
    def valid_status(cls, v):
        if v not in ("active", "inactive", "suspended"):
            raise ValueError("status must be active, inactive or suspended")
        return v


class UserOut(ORMModel):
    id: int
    full_name: str
    email: str
    phone: Optional[str] = None
    role_name: Optional[str] = None
    status: str
    is_email_verified: bool
    registration_status: Optional[str] = None
    deactivation_requested: bool = False
    last_login_at: Optional[datetime] = None
    created_at: datetime


class ActivityOut(ORMModel):
    id: int
    user_id: int
    action: str
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime

# ---------- profile ----------
class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    phone: Optional[str] = None
    email: Optional[EmailStr] = None

    _ph = field_validator("phone")(check_phone)


class ProfileHistoryOut(ORMModel):
    id: int
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_by: Optional[int] = None
    created_at: datetime


class DeactivationRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


# ---------- addresses ----------
class AddressBase(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str
    line1: str = Field(min_length=3, max_length=255)
    line2: Optional[str] = None
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    postal_code: str
    country: str = "India"
    address_type: str = "shipping"
    is_default: bool = False

    _ph = field_validator("phone")(check_phone)

    @field_validator("postal_code")
    @classmethod
    def pincode(cls, v):
        if not re.match(r"^[1-9]\d{5}$", v):
            raise ValueError("Postal code must be a valid 6 digit pincode")
        return v

    @field_validator("address_type")
    @classmethod
    def addr_type(cls, v):
        if v not in ("shipping", "billing"):
            raise ValueError("address_type must be shipping or billing")
        return v


class AddressCreate(AddressBase):
    pass


class AddressUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    line1: Optional[str] = None
    line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    address_type: Optional[str] = None

    _ph = field_validator("phone")(check_phone)

    @field_validator("postal_code")
    @classmethod
    def pincode(cls, v):
        if v is not None and not re.match(r"^[1-9]\d{5}$", v):
            raise ValueError("Postal code must be a valid 6 digit pincode")
        return v

    @field_validator("address_type")
    @classmethod
    def addr_type(cls, v):
        if v is not None and v not in ("shipping", "billing"):
            raise ValueError("address_type must be shipping or billing")
        return v


class AddressOut(ORMModel):
    id: int
    user_id: int
    full_name: str
    phone: str
    line1: str
    line2: Optional[str] = None
    city: str
    state: str
    postal_code: str
    country: str
    address_type: str
    is_default: bool
    created_at: datetime