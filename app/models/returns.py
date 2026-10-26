from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.utils import utcnow


class ReturnRequest(Base):
    __tablename__ = "return_requests"

    id = Column(Integer, primary_key=True)
    return_number = Column(String(40), unique=True, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(String(255), nullable=False)
    # requested, approved, rejected, received, refunded
    status = Column(String(20), default="requested")
    admin_note = Column(String(255))
    refund_id = Column(Integer, ForeignKey("refunds.id"), nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    items = relationship("ReturnItem", back_populates="return_request", cascade="all, delete-orphan")


class ReturnItem(Base):
    __tablename__ = "return_items"

    id = Column(Integer, primary_key=True)
    return_id = Column(Integer, ForeignKey("return_requests.id", ondelete="CASCADE"))
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    reason = Column(String(255))

    return_request = relationship("ReturnRequest", back_populates="items")


class Exchange(Base):
    __tablename__ = "exchanges"

    id = Column(Integer, primary_key=True)
    exchange_number = Column(String(40), unique=True, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    replacement_product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    replacement_variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)
    reason = Column(String(255))
    # requested, approved, rejected, item_received, replacement_shipped, completed
    status = Column(String(30), default="requested")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    history = relationship("ExchangeHistory", order_by="ExchangeHistory.id")


class ExchangeHistory(Base):
    __tablename__ = "exchange_history"

    id = Column(Integer, primary_key=True)
    exchange_id = Column(Integer, ForeignKey("exchanges.id", ondelete="CASCADE"))
    from_status = Column(String(30))
    to_status = Column(String(30))
    note = Column(String(255))
    changed_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)