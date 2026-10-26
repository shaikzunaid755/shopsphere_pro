from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text

from app.core.database import Base
from app.core.utils import utcnow


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title = Column(String(150), nullable=False)
    message = Column(String(500), nullable=False)
    notif_type = Column(String(30), default="system")  # order/payment/refund/inventory/system
    reference_type = Column(String(30))
    reference_id = Column(Integer)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)


class MessageProvider(Base):
    __tablename__ = "message_providers"

    id = Column(Integer, primary_key=True)
    channel = Column(String(10), nullable=False)  # email / sms
    name = Column(String(80), nullable=False)
    provider_type = Column(String(20), default="console")  # smtp / console
    config = Column(Text)  # json with host, port etc. (no passwords here, those stay in .env)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

    id = Column(Integer, primary_key=True)
    channel = Column(String(10), nullable=False)  # email / sms
    recipient = Column(String(150), nullable=False)
    subject = Column(String(200))
    body = Column(Text, nullable=False)
    template = Column(String(40))  # verification, order_confirmation, shipment, delivery ...
    status = Column(String(20), default="queued")  # queued / sent / failed
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    last_error = Column(String(500))
    provider_id = Column(Integer, ForeignKey("message_providers.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    sent_at = Column(DateTime)