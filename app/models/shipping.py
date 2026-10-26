from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.utils import utcnow


class ShippingProvider(Base):
    __tablename__ = "shipping_providers"

    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    code = Column(String(30), unique=True, nullable=False)
    contact_phone = Column(String(15))
    tracking_url = Column(String(255))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class Shipment(Base):
    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True)
    shipment_number = Column(String(40), unique=True, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    provider_id = Column(Integer, ForeignKey("shipping_providers.id"), nullable=True)
    tracking_number = Column(String(80), unique=True, nullable=True)
    # created, dispatched, in_transit, out_for_delivery, delivered, exception, returned
    status = Column(String(30), default="created")
    weight_kg = Column(String(20))
    dispatch_note = Column(String(255))
    dispatched_at = Column(DateTime)
    delivered_at = Column(DateTime)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)

    provider = relationship("ShippingProvider")
    events = relationship("TrackingEvent", back_populates="shipment", order_by="TrackingEvent.event_time")


class TrackingEvent(Base):
    __tablename__ = "tracking_events"

    id = Column(Integer, primary_key=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id", ondelete="CASCADE"), index=True)
    status = Column(String(30), nullable=False)
    location = Column(String(120))
    description = Column(String(255))
    is_exception = Column(Boolean, default=False)
    source = Column(String(20), default="manual")  # manual / provider
    event_time = Column(DateTime, default=utcnow)
    created_at = Column(DateTime, default=utcnow)

    shipment = relationship("Shipment", back_populates="events")


class Delivery(Base):
    __tablename__ = "deliveries"

    id = Column(Integer, primary_key=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), unique=True, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assignee_name = Column(String(120))  # delivery boy or provider name
    # assigned, out_for_delivery, delivered, failed
    status = Column(String(30), default="assigned")
    attempts_count = Column(Integer, default=0)
    received_by = Column(String(120))
    proof_note = Column(String(255))
    delivered_at = Column(DateTime)
    assigned_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)

    attempts = relationship("DeliveryAttempt", back_populates="delivery", order_by="DeliveryAttempt.attempt_no")


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id = Column(Integer, primary_key=True)
    delivery_id = Column(Integer, ForeignKey("deliveries.id", ondelete="CASCADE"))
    attempt_no = Column(Integer)
    status = Column(String(20))  # success / failed
    failure_reason = Column(String(255))
    note = Column(String(255))
    recorded_by = Column(Integer, ForeignKey("users.id"))
    attempted_at = Column(DateTime, default=utcnow)

    delivery = relationship("Delivery", back_populates="attempts")