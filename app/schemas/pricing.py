from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel


def _check_discount(discount_type, value):
    # used by coupons and promotions
    if discount_type not in ("percentage", "fixed"):
        raise ValueError("discount_type must be percentage or fixed")
    if discount_type == "percentage" and value is not None and value > 100:
        raise ValueError("percentage discount cannot be more than 100")

# ---------- coupons ----------
class CouponCreate(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    description: Optional[str] = None
    discount_type: str
    discount_value: Decimal = Field(gt=0)
    max_discount: Optional[Decimal] = Field(default=None, gt=0)
    min_purchase: Decimal = Field(default=0, ge=0)
    usage_limit: Optional[int] = Field(default=None, gt=0)
    per_user_limit: int = Field(default=1, gt=0)
    valid_from: datetime
    valid_to: datetime

    @model_validator(mode="after")
    def check(self):
        _check_discount(self.discount_type, self.discount_value)
        if self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self

class CouponUpdate(BaseModel):
    description: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[Decimal] = Field(default=None, gt=0)
    max_discount: Optional[Decimal] = None
    min_purchase: Optional[Decimal] = Field(default=None, ge=0)
    usage_limit: Optional[int] = None
    per_user_limit: Optional[int] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


class ActiveToggle(BaseModel):
    is_active: bool


class CouponOut(ORMModel):
    id: int
    code: str
    description: Optional[str] = None
    discount_type: str
    discount_value: Decimal
    max_discount: Optional[Decimal] = None
    min_purchase: Decimal
    usage_limit: Optional[int] = None
    per_user_limit: int
    used_count: int
    valid_from: datetime
    valid_to: datetime
    is_active: bool

# ---------- promotions ----------
class PromotionCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = None
    promo_type: str                      # product or category
    product_id: Optional[int] = None
    category_id: Optional[int] = None
    discount_type: str
    discount_value: Decimal = Field(gt=0)
    min_quantity: int = Field(default=1, ge=1)
    min_order_amount: Decimal = Field(default=0, ge=0)
    start_date: datetime
    end_date: datetime

    @model_validator(mode="after")
    def check(self):
        _check_discount(self.discount_type, self.discount_value)
        if self.promo_type not in ("product", "category"):
            raise ValueError("promo_type must be product or category")
        if self.promo_type == "product" and not self.product_id:
            raise ValueError("product promotion needs product_id")
        if self.promo_type == "category" and not self.category_id:
            raise ValueError("category promotion needs category_id")
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self

class PromotionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    discount_value: Optional[Decimal] = Field(default=None, gt=0)
    min_quantity: Optional[int] = Field(default=None, ge=1)
    min_order_amount: Optional[Decimal] = Field(default=None, ge=0)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class PromotionHistoryOut(ORMModel):
    id: int
    promotion_id: int
    action: str
    details: Optional[str] = None
    changed_by: Optional[int] = None
    created_at: datetime

class PromotionOut(ORMModel):
    id: int
    name: str
    description: Optional[str] = None
    promo_type: str
    product_id: Optional[int] = None
    category_id: Optional[int] = None
    discount_type: str
    discount_value: Decimal
    min_quantity: int
    min_order_amount: Decimal
    start_date: datetime
    end_date: datetime
    is_active: bool
# ---------- tax ----------
class TaxRuleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    rate: Decimal = Field(ge=0, le=100)
    product_id: Optional[int] = None
    category_id: Optional[int] = None

    @model_validator(mode="after")
    def check(self):
        # swagger example sends 0 for both, treat as "not set"
        self.product_id = self.product_id or None
        self.category_id = self.category_id or None
        if self.product_id and self.category_id:
            raise ValueError("A tax rule is for a product OR a category, not both")
        return self


class TaxRuleUpdate(BaseModel):
    name: Optional[str] = None
    rate: Optional[Decimal] = Field(default=None, ge=0, le=100)
    is_active: Optional[bool] = None

class TaxRuleOut(ORMModel):
    id: int
    name: str
    rate: Decimal
    product_id: Optional[int] = None
    category_id: Optional[int] = None
    is_active: bool


class TaxHistoryOut(ORMModel):
    id: int
    tax_rule_id: int
    action: str
    old_rate: Optional[Decimal] = None
    new_rate: Optional[Decimal] = None
    changed_by: Optional[int] = None
    created_at: datetime


class TaxCalcRequest(BaseModel):
    product_id: int
    amount: Decimal = Field(gt=0)

# ---------- shipping ----------
class ShippingMethodCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    code: str = Field(min_length=2, max_length=30)
    description: Optional[str] = None
    base_charge: Decimal = Field(default=0, ge=0)
    per_item_charge: Decimal = Field(default=0, ge=0)
    free_above: Optional[Decimal] = Field(default=None, ge=0)
    min_order_amount: Decimal = Field(default=0, ge=0)
    max_order_amount: Optional[Decimal] = Field(default=None, gt=0)
    allowed_states: Optional[str] = None      # "Telangana,Karnataka", empty = all
    cod_supported: bool = True
    estimated_days: int = Field(default=5, ge=0)

class ShippingMethodUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    base_charge: Optional[Decimal] = Field(default=None, ge=0)
    per_item_charge: Optional[Decimal] = Field(default=None, ge=0)
    free_above: Optional[Decimal] = None
    min_order_amount: Optional[Decimal] = None
    max_order_amount: Optional[Decimal] = None
    allowed_states: Optional[str] = None
    cod_supported: Optional[bool] = None
    estimated_days: Optional[int] = None


class ShippingMethodOut(ShippingMethodCreate):
    # same fields as create, plus id and status
    model_config = {"from_attributes": True}
    id: int
    is_active: bool

class ShippingQuoteRequest(BaseModel):
    order_amount: Decimal = Field(ge=0)
    item_count: int = Field(default=1, gt=0)
    state: Optional[str] = None
    cod: bool = False


class ShippingQuote(BaseModel):
    method_id: int
    name: str
    charge: Decimal
    estimated_days: int
