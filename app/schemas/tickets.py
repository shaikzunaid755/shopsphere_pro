# Module 40: Customer Support Tickets - schemas
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["order", "payment", "delivery", "return", "product", "account", "other"]
Priority = Literal["low", "medium", "high", "urgent"]


class TicketCreate(BaseModel):
    subject: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=10, max_length=3000)
    category: Category = "other"
    priority: Priority = "medium"
    order_id: Optional[int] = Field(None, gt=0)
    product_id: Optional[int] = Field(None, gt=0)


class TicketAssign(BaseModel):
    agent_id: int = Field(gt=0)
    note: Optional[str] = Field(None, max_length=255)


class TicketPriority(BaseModel):
    priority: Priority
    reason: str = Field(min_length=5, max_length=255)


class TicketMessageCreate(BaseModel):
    message: str = Field(min_length=2, max_length=3000)
    # internal notes are only for staff, customer never sees them
    is_internal: bool = False


class TicketResolve(BaseModel):
    resolution: str = Field(min_length=10, max_length=3000)


class TicketReason(BaseModel):
    reason: Optional[str] = Field(None, max_length=255)


class TicketMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender_id: Optional[int] = None
    sender_name: Optional[str] = None
    message: str
    is_internal: bool = False
    created_at: Optional[datetime] = None


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_number: str
    user_id: int
    order_id: Optional[int] = None
    product_id: Optional[int] = None
    subject: str
    description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[int] = None
    resolution: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class TicketDetail(TicketOut):
    messages: List[TicketMessageOut] = []
