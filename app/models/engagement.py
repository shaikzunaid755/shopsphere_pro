from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.utils import utcnow


class Review(Base):
    __tablename__ = "reviews"
    # one review per customer per product
    __table_args__ = (UniqueConstraint("product_id", "user_id", name="uq_review_user_product"),)

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    title = Column(String(150))
    comment = Column(Text)
    status = Column(String(20), default="published")  # published / hidden / removed
    moderated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    moderation_note = Column(String(255))
    created_at = Column(DateTime, default=utcnow)


class ProductQuestion(Base):
    __tablename__ = "product_questions"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question = Column(Text, nullable=False)
    status = Column(String(20), default="open")  # open / answered / hidden
    created_at = Column(DateTime, default=utcnow)

    answers = relationship("ProductAnswer", back_populates="question")


class ProductAnswer(Base):
    __tablename__ = "product_answers"

    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("product_questions.id", ondelete="CASCADE"))
    answered_by = Column(Integer, ForeignKey("users.id"))
    answer = Column(Text, nullable=False)
    status = Column(String(20), default="visible")  # visible / hidden
    created_at = Column(DateTime, default=utcnow)

    question = relationship("ProductQuestion", back_populates="answers")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True)
    ticket_number = Column(String(40), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    subject = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    category = Column(String(40), default="general")
    priority = Column(String(20), default="medium")  # low / medium / high / urgent
    # open, assigned, in_progress, resolved, closed
    status = Column(String(20), default="open")
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolution = Column(Text)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    resolved_at = Column(DateTime)

    messages = relationship("TicketMessage", back_populates="ticket", order_by="TicketMessage.id")


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"))
    sender_id = Column(Integer, ForeignKey("users.id"))
    message = Column(Text, nullable=False)
    is_internal = Column(Boolean, default=False)  # staff only notes
    created_at = Column(DateTime, default=utcnow)

    ticket = relationship("SupportTicket", back_populates="messages")