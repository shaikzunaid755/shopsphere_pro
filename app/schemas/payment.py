# Module 29 + 30: request / response schemas for payments
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class PaymentCreate(BaseModel):
    order_id: int = Field(gt=0)
    method: Literal["card", "cod"] = "card"


class PaymentCreated(BaseModel):
    payment_id: int
    order_id: int
    amount: Decimal
    currency: str
    method: str
    gateway: str
    status: str
    gateway_reference: Optional[str] = None
    client_secret: Optional[str] = None


class SandboxConfirm(BaseModel):
    # only for testing without stripe: decide if the payment passes or fails
    result: Literal["success", "fail"] = "success"
    failure_reason: Optional[str] = Field(None, max_length=255)


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    user_id: int
    amount: Decimal
    currency: Optional[str] = None
    method: Optional[str] = None
    gateway: Optional[str] = None
    gateway_reference: Optional[str] = None
    status: str
    failure_reason: Optional[str] = None
    refunded_amount: Optional[Decimal] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_id: Optional[int] = None
    order_id: Optional[int] = None
    event_type: Optional[str] = None
    status: Optional[str] = None
    amount: Optional[Decimal] = None
    gateway_reference: Optional[str] = None
    created_at: Optional[datetime] = None


# Module 31: refunds
class RefundCreate(BaseModel):
    order_id: int = Field(gt=0)
    # leave empty for a full refund of whatever is still refundable
    amount: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=3, max_length=255)
    # staff (finance / admin) can send the money straight away
    process_now: bool = False


class RefundReject(BaseModel):
    note: str = Field(min_length=3, max_length=255)


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    refund_number: str
    order_id: int
    payment_id: Optional[int] = None
    amount: Decimal
    refund_type: Optional[str] = None
    reason: Optional[str] = None
    status: str
    gateway_reference: Optional[str] = None
    requested_by: Optional[int] = None
    processed_by: Optional[int] = None
    note: Optional[str] = None
    created_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None


# Module 32: cash on delivery
class CODRuleUpdate(BaseModel):
    is_enabled: Optional[bool] = None
    min_order_amount: Optional[Decimal] = Field(None, ge=0, max_digits=12, decimal_places=2)
    max_order_amount: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=2)
    # comma separated 6 digit pincodes, "" clears the list
    blocked_postal_codes: Optional[str] = Field(None, max_length=1000, pattern=r"^(\s*\d{6}\s*)(,\s*\d{6}\s*)*$|^$")


class CODRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_enabled: bool
    min_order_amount: Decimal
    max_order_amount: Decimal
    blocked_postal_codes: Optional[str] = None
    updated_by: Optional[int] = None
    updated_at: Optional[datetime] = None


class CODEligibilityCheck(BaseModel):
    shipping_address_id: int
    shipping_method_id: int
    coupon_code: Optional[str] = None


class CODCollect(BaseModel):
    collected_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    note: Optional[str] = Field(None, max_length=255)


class CODFail(BaseModel):
    note: str = Field(min_length=3, max_length=255)


class CODReconcile(BaseModel):
    order_ids: Optional[list[int]] = None  # empty = every collected order
    accept_mismatch: bool = False
    note: Optional[str] = Field(None, max_length=255)


class CODCollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    expected_amount: Decimal
    collected_amount: Optional[Decimal] = None
    status: str
    collected_by: Optional[int] = None
    collected_at: Optional[datetime] = None
    reconciled_by: Optional[int] = None
    reconciled_at: Optional[datetime] = None
    note: Optional[str] = None
    created_at: Optional[datetime] = None
