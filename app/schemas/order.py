from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class CheckoutRequest(BaseModel):
    shipping_address_id: int
    billing_address_id: Optional[int] = None  # same as shipping if empty
    shipping_method_id: int
    payment_method: str = "online"
    coupon_code: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=255)

    @field_validator("payment_method")
    @classmethod
    def pm(cls, v):
        if v not in ("online", "cod"):
            raise ValueError("payment_method must be online or cod")
        return v


class CheckoutValidateRequest(BaseModel):
    shipping_address_id: int
    billing_address_id: Optional[int] = None
    shipping_method_id: int
    payment_method: str = "online"
    coupon_code: Optional[str] = None


class OrderItemOut(ORMModel):
    id: int
    product_id: int
    variant_id: Optional[int] = None
    warehouse_id: Optional[int] = None
    product_name: Optional[str] = None
    variant_name: Optional[str] = None
    sku: Optional[str] = None
    description_snapshot: Optional[str] = None
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    line_total: Decimal
    fulfillment_status: str


class StatusHistoryOut(ORMModel):
    id: int
    from_status: Optional[str] = None
    to_status: str
    note: Optional[str] = None
    changed_by: Optional[int] = None
    created_at: datetime


class OrderOut(ORMModel):
    id: int
    order_number: str
    user_id: int
    status: str
    payment_method: str
    payment_status: str
    shipping_address_text: Optional[str] = None
    billing_address_text: Optional[str] = None
    shipping_method_id: Optional[int] = None
    coupon_code: Optional[str] = None
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    shipping_total: Decimal
    grand_total: Decimal
    notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class OrderDetail(OrderOut):
    items: List[OrderItemOut] = []
    status_history: List[StatusHistoryOut] = []
    tax_breakdown_list: Any = None


class OrderList(BaseModel):
    total: int
    page: int
    size: int
    items: List[OrderOut]


class StatusChange(BaseModel):
    status: str
    note: Optional[str] = None


class ItemFulfillmentUpdate(BaseModel):
    fulfillment_status: str

    @field_validator("fulfillment_status")
    @classmethod
    def ok(cls, v):
        if v not in ("pending", "packed", "shipped", "delivered"):
            raise ValueError("use pending, packed, shipped or delivered")
        return v


class LineTotal(BaseModel):
    item_id: int
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal


class CancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


class CancellationOut(ORMModel):
    id: int
    order_id: int
    requested_by: Optional[int] = None
    reason: str
    status: str
    outcome: Optional[str] = None
    refund_id: Optional[int] = None
    created_at: datetime