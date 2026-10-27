from datetime import datetime
from decimal import Decimal
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class CartItemAdd(BaseModel):
    product_id: int
    variant_id: Optional[int] = None
    quantity: int = Field(default=1, gt=0, le=20)

    @field_validator("variant_id")
    @classmethod
    def zero_is_none(cls, v):
        # swagger example sends 0, means no variant
        return v or None


class CartItemUpdate(BaseModel):
    quantity: int = Field(gt=0, le=20)

class CartLine(BaseModel):
    item_id: int
    product_id: int
    variant_id: Optional[int] = None
    name: str
    variant_name: Optional[str] = None
    sku: str
    quantity: int
    unit_price: Decimal
    line_amount: Decimal
    available_stock: int
    in_stock: bool


class CartOut(BaseModel):
    cart_id: int
    items: List[CartLine]
    item_count: int
    subtotal: Decimal
    coupon_code: Optional[str] = None
    warnings: List[str] = []

class ApplyCoupon(BaseModel):
    code: str = Field(min_length=2, max_length=40)


class CartSummaryRequest(BaseModel):
    coupon_code: Optional[str] = None
    shipping_method_id: Optional[int] = None
    address_id: Optional[int] = None
    payment_method: str = "online"

    @field_validator("shipping_method_id", "address_id")
    @classmethod
    def zero_is_none(cls, v):
        return v or None

    @field_validator("coupon_code")
    @classmethod
    def blank_coupon(cls, v):
        # swagger sends "string" as example text
        return None if not v or v == "string" else v

class SummaryLine(BaseModel):
    product_id: int
    variant_id: Optional[int] = None
    name: str
    sku: str
    quantity: int
    unit_price: Decimal
    line_amount: Decimal
    promotion_discount: Decimal
    coupon_discount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    line_total: Decimal


class TaxLine(BaseModel):
    tax: str
    amount: Decimal

class CheckoutSummary(BaseModel):
    lines: List[SummaryLine]
    item_count: int
    subtotal: Decimal
    promotion_discount: Decimal
    coupon_code: Optional[str] = None
    coupon_discount: Decimal
    discount_total: Decimal
    tax_total: Decimal
    tax_breakdown: List[TaxLine]
    shipping_method: Optional[str] = None
    shipping_total: Decimal
    grand_total: Decimal
    errors: List[str] = []

class WishlistAdd(BaseModel):
    product_id: int
    variant_id: Optional[int] = None

    @field_validator("variant_id")
    @classmethod
    def zero_is_none(cls, v):
        return v or None


class WishlistOut(BaseModel):
    id: int
    product_id: int
    variant_id: Optional[int] = None
    product_name: str
    current_price: Decimal
    status: str
    available: int
    in_stock: bool
    created_at: datetime