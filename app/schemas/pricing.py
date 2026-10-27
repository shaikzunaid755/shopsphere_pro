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
