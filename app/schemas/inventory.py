from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ORMModel


class WarehouseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    code: str = Field(min_length=2, max_length=20)
    address: Optional[str] = None
    city: Optional[str] = None
    capacity: int = Field(default=0, ge=0)


class WarehouseUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    capacity: Optional[int] = Field(default=None, ge=0)


class WarehouseStatus(BaseModel):
    status: str

    @model_validator(mode="after")
    def ok(self):
        if self.status not in ("active", "inactive"):
            raise ValueError("status must be active or inactive")
        return self


class WarehouseOut(ORMModel):
    id: int
    name: str
    code: str
    address: Optional[str] = None
    city: Optional[str] = None
    capacity: int
    status: str


class WarehouseUsage(WarehouseOut):
    used_units: int
    free_units: Optional[int] = None


class StockChange(BaseModel):
    product_id: int
    variant_id: Optional[int] = None
    warehouse_id: int
    quantity: int = Field(gt=0)
    note: Optional[str] = None

    @field_validator("variant_id")
    @classmethod
    def zero_is_none(cls, v):
        # swagger example body sends 0, treat it as "no variant"
        return v or None


class InventoryOut(ORMModel):
    id: int
    product_id: int
    variant_id: Optional[int] = None
    warehouse_id: int
    quantity: int
    reserved: int
    available: int
    min_threshold: int
    updated_at: Optional[datetime] = None


class MovementOut(ORMModel):
    id: int
    product_id: int
    variant_id: Optional[int] = None
    warehouse_id: int
    movement_type: str
    quantity: int
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    note: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime


class TransferItemIn(BaseModel):
    product_id: int
    variant_id: Optional[int] = None
    quantity: int = Field(gt=0)

    @field_validator("variant_id")
    @classmethod
    def zero_is_none(cls, v):
        return v or None


class TransferCreate(BaseModel):
    from_warehouse_id: int
    to_warehouse_id: int
    items: List[TransferItemIn] = Field(min_length=1)
    note: Optional[str] = None

    @model_validator(mode="after")
    def diff(self):
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValueError("Source and destination warehouse must be different")
        return self


class TransferQty(BaseModel):
    item_id: int
    quantity: int = Field(ge=0)


class TransferDispatch(BaseModel):
    # if empty, full requested qty is dispatched
    items: List[TransferQty] = []


class TransferReceive(BaseModel):
    items: List[TransferQty] = []


class TransferItemOut(ORMModel):
    id: int
    product_id: int
    variant_id: Optional[int] = None
    requested_qty: int
    dispatched_qty: int
    received_qty: int


class TransferOut(ORMModel):
    id: int
    transfer_number: str
    from_warehouse_id: int
    to_warehouse_id: int
    status: str
    note: Optional[str] = None
    requested_by: Optional[int] = None
    created_at: datetime
    dispatched_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    items: List[TransferItemOut] = []


class ThresholdUpdate(BaseModel):
    inventory_id: int
    min_threshold: int = Field(ge=0)


class ThresholdHistoryOut(ORMModel):
    id: int
    inventory_id: int
    old_value: Optional[int] = None
    new_value: int
    changed_by: Optional[int] = None
    created_at: datetime


class AlertOut(ORMModel):
    id: int
    inventory_id: int
    product_id: int
    warehouse_id: int
    available_qty: int
    threshold: int
    status: str
    created_at: datetime
    resolved_at: Optional[datetime] = None


class LowStockRow(BaseModel):
    inventory_id: int
    product_id: int
    product_name: str
    variant_id: Optional[int] = None
    warehouse_id: int
    warehouse_code: str
    available: int
    threshold: int
    shortfall: int