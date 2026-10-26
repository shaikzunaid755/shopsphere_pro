from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Numeric, Text

from app.core.database import Base
from app.core.utils import utcnow


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(10), default="inr")
    method = Column(String(20), default="card")  # card / cod
    gateway = Column(String(20), default="sandbox")  # stripe / sandbox / cod
    gateway_reference = Column(String(120), unique=True, nullable=True)
    client_secret = Column(String(255))
    # pending, succeeded, failed, cancelled
    status = Column(String(20), default="pending")
    failure_reason = Column(String(255))
    idempotency_key = Column(String(80), unique=True, nullable=True)
    refunded_amount = Column(Numeric(12, 2), default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class PaymentTransaction(Base):
    # every attempt / gateway event is stored here, never edited
    __tablename__ = "payment_transactions"

    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), index=True)
    event_type = Column(String(40))  # created, succeeded, failed, refund_processed ...
    status = Column(String(20))
    amount = Column(Numeric(12, 2))
    gateway_reference = Column(String(120))
    raw_payload = Column(Text)
    created_at = Column(DateTime, default=utcnow)


class WebhookEvent(Base):
    # used to ignore the same webhook if gateway sends it twice
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(120), unique=True, nullable=False)
    event_type = Column(String(60))
    processed_at = Column(DateTime, default=utcnow)


class Refund(Base):
    __tablename__ = "refunds"

    id = Column(Integer, primary_key=True)
    refund_number = Column(String(40), unique=True, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    refund_type = Column(String(20), default="full")  # full / partial
    reason = Column(String(255))
    # requested, processed, rejected, failed
    status = Column(String(20), default="requested")
    gateway_reference = Column(String(120))
    requested_by = Column(Integer, ForeignKey("users.id"))
    processed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(String(255))
    created_at = Column(DateTime, default=utcnow)
    processed_at = Column(DateTime)


class CODRule(Base):
    # single row config for cash on delivery
    __tablename__ = "cod_rules"

    id = Column(Integer, primary_key=True)
    is_enabled = Column(Boolean, default=True)
    max_order_amount = Column(Numeric(12, 2), default=10000)
    min_order_amount = Column(Numeric(12, 2), default=0)
    blocked_postal_codes = Column(String(1000))  # comma separated
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class CODCollection(Base):
    __tablename__ = "cod_collections"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), unique=True, nullable=False)
    expected_amount = Column(Numeric(12, 2), nullable=False)
    collected_amount = Column(Numeric(12, 2), default=0)
    # pending, collected, short_collected, failed, reconciled
    status = Column(String(20), default="pending")
    collected_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    collected_at = Column(DateTime)
    reconciled_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reconciled_at = Column(DateTime)
    note = Column(String(255))
    created_at = Column(DateTime, default=utcnow)