# Module 36: Return Management - request / response schemas
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReturnItemIn(BaseModel):
    order_item_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)
    reason: Optional[str] = Field(None, max_length=255)


class ReturnCreate(BaseModel):
    order_id: int = Field(gt=0)
    reason: str = Field(min_length=5, max_length=255)
    items: List[ReturnItemIn] = Field(min_length=1, max_length=20)


class ReturnDecision(BaseModel):
    note: Optional[str] = Field(None, max_length=255)


class ReturnReject(BaseModel):
    note: str = Field(min_length=5, max_length=255)


class ReturnItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_item_id: int
    quantity: int
    reason: Optional[str] = None


class ReturnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    return_number: str
    order_id: int
    user_id: int
    reason: str
    status: str
    admin_note: Optional[str] = None
    refund_id: Optional[int] = None
    reviewed_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    items: List[ReturnItemOut] = []
