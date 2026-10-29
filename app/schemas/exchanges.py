# Module 37: Exchange Management - request / response schemas
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExchangeCreate(BaseModel):
    order_id: int = Field(gt=0)
    order_item_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)
    replacement_product_id: int = Field(gt=0)
    replacement_variant_id: Optional[int] = None
    reason: str = Field(min_length=5, max_length=255)


class ExchangeNote(BaseModel):
    note: Optional[str] = Field(None, max_length=255)


class ExchangeReject(BaseModel):
    note: str = Field(min_length=5, max_length=255)


class ItemReceived(BaseModel):
    # damaged item should not go back on the shelf
    restock: bool = False
    note: Optional[str] = Field(None, max_length=255)


class ExchangeHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    note: Optional[str] = None
    changed_by: Optional[int] = None
    created_at: Optional[datetime] = None


class ExchangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exchange_number: str
    order_id: int
    order_item_id: int
    user_id: int
    quantity: int
    replacement_product_id: int
    replacement_variant_id: Optional[int] = None
    warehouse_id: Optional[int] = None
    reason: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    history: List[ExchangeHistoryOut] = []
