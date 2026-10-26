from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.utils import utcnow


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(40), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # pending, confirmed, processing, shipped, delivered, cancelled
    status = Column(String(20), default="pending", index=True)
    payment_method = Column(String(20), default="online")  # online / cod
    # pending, paid, failed, refunded, partially_refunded, cod_pending, cod_collected
    payment_status = Column(String(30), default="pending")

    shipping_address_id = Column(Integer, ForeignKey("addresses.id"))
    billing_address_id = Column(Integer, ForeignKey("addresses.id"))
    shipping_address_text = Column(Text)
    billing_address_text = Column(Text)
    shipping_method_id = Column(Integer, ForeignKey("shipping_methods.id"))
    coupon_code = Column(String(40))

    subtotal = Column(Numeric(12, 2), default=0)
    discount_total = Column(Numeric(12, 2), default=0)
    tax_total = Column(Numeric(12, 2), default=0)
    shipping_total = Column(Numeric(12, 2), default=0)
    grand_total = Column(Numeric(12, 2), default=0)
    tax_breakdown = Column(Text)  # json list

    idempotency_key = Column(String(80), unique=True, nullable=True)
    notes = Column(String(255))
    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    status_history = relationship("OrderStatusHistory", back_populates="order",
                                  order_by="OrderStatusHistory.id")
    user = relationship("User")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)

    # snapshot so later product edits don't change old orders
    product_name = Column(String(200))
    variant_name = Column(String(120))
    sku = Column(String(60))
    description_snapshot = Column(Text)

    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    discount_amount = Column(Numeric(12, 2), default=0)
    tax_rate = Column(Numeric(5, 2), default=0)
    tax_amount = Column(Numeric(12, 2), default=0)
    line_total = Column(Numeric(12, 2), nullable=False)
    # pending, packed, shipped, delivered, cancelled, returned, exchanged
    fulfillment_status = Column(String(20), default="pending")

    order = relationship("Order", back_populates="items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status = Column(String(20))
    to_status = Column(String(20))
    note = Column(String(255))
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    order = relationship("Order", back_populates="status_history")


class OrderCancellation(Base):
    __tablename__ = "order_cancellations"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), unique=True, nullable=False)
    requested_by = Column(Integer, ForeignKey("users.id"))
    reason = Column(String(255), nullable=False)
    status = Column(String(20), default="approved")  # approved / rejected
    outcome = Column(String(255))
    refund_id = Column(Integer, ForeignKey("refunds.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)