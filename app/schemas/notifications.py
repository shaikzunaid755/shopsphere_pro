# Module 41 + 42: Notifications, Email and SMS - schemas
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    message: str
    notif_type: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    is_read: bool = False
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class BroadcastCreate(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    message: str = Field(min_length=3, max_length=1000)
    # send to one user or to every active user of some roles
    user_id: Optional[int] = Field(None, gt=0)
    roles: List[str] = []
    notif_type: str = Field("system", max_length=30)

    @model_validator(mode="after")
    def need_target(self):
        if not self.user_id and not self.roles:
            raise ValueError("Give a user_id or at least one role")
        return self


class EventNotify(BaseModel):
    event: Literal["order_status", "payment_status", "refund_status"]
    reference_id: int = Field(gt=0)
    sms: bool = False


class ProviderCreate(BaseModel):
    channel: Literal["email", "sms"]
    name: str = Field(min_length=3, max_length=60)
    # console = log only (dev), smtp = real mail server, twilio / msg91 need paid keys
    provider_type: Literal["console", "smtp", "twilio", "msg91"]
    config: Dict[str, str] = {}
    is_active: bool = True


class ProviderUpdate(BaseModel):
    config: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None


class ProviderOut(BaseModel):
    id: int
    channel: str
    name: str
    provider_type: str
    config: Dict[str, str] = {}
    is_active: bool
    created_at: Optional[datetime] = None


class TemplateSend(BaseModel):
    template: Literal["email_verification", "order_confirmation", "shipment_update", "delivery_update"]
    # user id for email_verification, order id for the others
    reference_id: int = Field(gt=0)
    sms: bool = False
    send_now: bool = True


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel: str
    recipient: str
    subject: Optional[str] = None
    body: str
    template: Optional[str] = None
    status: str
    attempts: int
    max_attempts: int
    last_error: Optional[str] = None
    provider_id: Optional[int] = None
    user_id: Optional[int] = None
    created_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
