# Module 33: Shipping Order Management - request / response schemas
from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProviderCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    contact_phone: Optional[str] = Field(None, pattern=r"^[6-9]\d{9}$")
    # use {tracking} where the tracking number should go, e.g. https://track.dtdc.in/?awb={tracking}
    tracking_url: Optional[str] = Field(None, max_length=255)


class ProviderUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=80)
    contact_phone: Optional[str] = Field(None, pattern=r"^[6-9]\d{9}$")
    tracking_url: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    contact_phone: Optional[str] = None
    tracking_url: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None


class ShipmentCreate(BaseModel):
    order_id: int = Field(gt=0)
    provider_id: Optional[int] = None
    weight_kg: Optional[Decimal] = Field(None, gt=0, le=500, decimal_places=2)
    dispatch_note: Optional[str] = Field(None, max_length=255)


class AssignProvider(BaseModel):
    provider_id: int
    # leave empty and we make one from the provider code
    tracking_number: Optional[str] = Field(None, min_length=4, max_length=80, pattern=r"^[A-Za-z0-9-]+$")


class DispatchIn(BaseModel):
    dispatch_note: Optional[str] = Field(None, max_length=255)
    weight_kg: Optional[Decimal] = Field(None, gt=0, le=500, decimal_places=2)
    location: Optional[str] = Field(None, max_length=120)


class TrackingUpdate(BaseModel):
    status: Literal["in_transit", "out_for_delivery", "delivered", "exception", "returned"]
    location: Optional[str] = Field(None, max_length=120)
    description: Optional[str] = Field(None, max_length=255)
    event_time: Optional[datetime] = None


class TrackingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    location: Optional[str] = None
    description: Optional[str] = None
    is_exception: bool = False
    source: Optional[str] = None
    event_time: Optional[datetime] = None


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_number: str
    order_id: int
    provider_id: Optional[int] = None
    provider: Optional[ProviderOut] = None
    tracking_number: Optional[str] = None
    status: str
    weight_kg: Optional[str] = None
    dispatch_note: Optional[str] = None
    dispatched_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    events: List[TrackingEventOut] = []


# Module 34: shipment tracking
class ProviderTrackingUpdate(BaseModel):
    # what a courier sends to our webhook
    event_id: str = Field(min_length=1, max_length=80)
    tracking_number: str = Field(min_length=4, max_length=80)
    status: str = Field(min_length=2, max_length=40)
    location: Optional[str] = Field(None, max_length=120)
    description: Optional[str] = Field(None, max_length=255)
    event_time: Optional[datetime] = None


class TrackingNumberUpdate(BaseModel):
    tracking_number: str = Field(min_length=4, max_length=80, pattern=r"^[A-Za-z0-9-]+$")
    reason: str = Field(min_length=3, max_length=200)


class ExceptionRaise(BaseModel):
    reason: Literal["address_not_found", "customer_unavailable", "refused", "damaged", "lost", "weather", "other"]
    note: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=120)


class ExceptionResolve(BaseModel):
    action: Literal["reattempt", "return_to_origin"]
    note: str = Field(min_length=3, max_length=200)


# Module 35: delivery management
class DeliveryAssign(BaseModel):
    shipment_id: int = Field(gt=0)
    # give a delivery person, or set use_courier=true to leave it with the shipment's courier
    assigned_user_id: Optional[int] = None
    use_courier: bool = False


class DeliveryReassign(BaseModel):
    assigned_user_id: Optional[int] = None
    use_courier: bool = False
    reason: str = Field(min_length=3, max_length=200)


class DeliveryConfirm(BaseModel):
    received_by: str = Field(min_length=2, max_length=120)
    # OTP told by customer / "signed" / "left with security" etc.
    proof_note: str = Field(min_length=3, max_length=255)
    note: Optional[str] = Field(None, max_length=255)


class DeliveryFail(BaseModel):
    failure_reason: Literal["customer_unavailable", "wrong_address", "refused", "cash_not_ready",
                            "premises_closed", "other"]
    note: Optional[str] = Field(None, max_length=255)


class DeliveryAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    attempt_no: int
    status: str
    failure_reason: Optional[str] = None
    note: Optional[str] = None
    recorded_by: Optional[int] = None
    attempted_at: Optional[datetime] = None


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_id: int
    order_id: int
    assigned_user_id: Optional[int] = None
    assignee_name: Optional[str] = None
    status: str
    attempts_count: int = 0
    received_by: Optional[str] = None
    proof_note: Optional[str] = None
    delivered_at: Optional[datetime] = None
    assigned_by: Optional[int] = None
    created_at: Optional[datetime] = None
    attempts: List[DeliveryAttemptOut] = []
