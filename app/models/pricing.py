from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Numeric, Text

from app.core.database import Base
from app.core.utils import utcnow


class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False)
    description = Column(String(255))
    discount_type = Column(String(20), nullable=False)  # percentage / fixed
    discount_value = Column(Numeric(10, 2), nullable=False)
    max_discount = Column(Numeric(10, 2), nullable=True)
    min_purchase = Column(Numeric(12, 2), default=0)
    usage_limit = Column(Integer, nullable=True)  # total uses, null = unlimited
    per_user_limit = Column(Integer, default=1)
    used_count = Column(Integer, default=0)
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)


class CouponUsage(Base):
    __tablename__ = "coupon_usages"

    id = Column(Integer, primary_key=True)
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    discount_amount = Column(Numeric(10, 2))
    created_at = Column(DateTime, default=utcnow)


class Promotion(Base):
    __tablename__ = "promotions"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    description = Column(String(255))
    promo_type = Column(String(20), nullable=False)  # product / category
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    discount_type = Column(String(20), nullable=False)  # percentage / fixed
    discount_value = Column(Numeric(10, 2), nullable=False)
    # eligibility rules
    min_quantity = Column(Integer, default=1)
    min_order_amount = Column(Numeric(12, 2), default=0)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)


class PromotionHistory(Base):
    __tablename__ = "promotion_history"

    id = Column(Integer, primary_key=True)
    promotion_id = Column(Integer, ForeignKey("promotions.id", ondelete="CASCADE"))
    action = Column(String(30))  # created / updated / activated / deactivated / applied
    details = Column(Text)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class TaxRule(Base):
    __tablename__ = "tax_rules"

    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    rate = Column(Numeric(5, 2), nullable=False)  # percent, e.g. 18.00
    # product rule beats category rule, category rule beats default (both null)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class TaxHistory(Base):
    __tablename__ = "tax_history"

    id = Column(Integer, primary_key=True)
    tax_rule_id = Column(Integer, ForeignKey("tax_rules.id", ondelete="CASCADE"))
    action = Column(String(30))
    old_rate = Column(Numeric(5, 2))
    new_rate = Column(Numeric(5, 2))
    changed_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)


class ShippingMethod(Base):
    __tablename__ = "shipping_methods"

    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    code = Column(String(30), unique=True, nullable=False)
    description = Column(String(255))
    base_charge = Column(Numeric(10, 2), default=0)
    per_item_charge = Column(Numeric(10, 2), default=0)
    free_above = Column(Numeric(12, 2), nullable=True)
    # eligibility
    min_order_amount = Column(Numeric(12, 2), default=0)
    max_order_amount = Column(Numeric(12, 2), nullable=True)
    allowed_states = Column(String(500), nullable=True)  # comma separated, empty = all
    cod_supported = Column(Boolean, default=True)
    estimated_days = Column(Integer, default=5)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)