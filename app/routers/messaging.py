# Module 42: Email and SMS Notifications
import secrets
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles
from app.core.roles import ADMIN
from app.core.utils import from_json, money, to_json, utcnow
from app.models import EmailVerification, MessageProvider, Order, OutboundMessage, Shipment, User
from app.schemas.notifications import ProviderCreate, ProviderUpdate, ProviderOut, TemplateSend, MessageOut
from app.services.notification_service import queue_message, send_message, process_outbox

router = APIRouter(prefix="/messages", tags=["42 Email and SMS Notifications"])

admin_only = require_roles(ADMIN)
message_staff = require_roles(ADMIN, "order_manager", "support_agent")
SECRET_KEYS = ("password", "api_key", "auth_token", "secret")


def _provider_out(p):
    cfg = from_json(p.config) or {}
    # never show passwords / keys back in the api
    shown = {k: ("****" if any(s in k.lower() for s in SECRET_KEYS) else v) for k, v in cfg.items()}
    return ProviderOut(id=p.id, channel=p.channel, name=p.name, provider_type=p.provider_type, config=shown,
                       is_active=bool(p.is_active), created_at=p.created_at)


def _only_active(db, p):
    # one active provider per channel, new active one switches the others off
    if p.is_active:
        (db.query(MessageProvider)
         .filter(MessageProvider.channel == p.channel, MessageProvider.id != p.id)
         .update({"is_active": False}, synchronize_session=False))


# ---------------- providers ----------------

@router.get("/providers", response_model=List[ProviderOut])
def list_providers(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return [_provider_out(p) for p in db.query(MessageProvider).order_by(MessageProvider.id).all()]


@router.post("/providers", response_model=ProviderOut, status_code=201)
def add_provider(data: ProviderCreate, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if data.channel == "email" and data.provider_type in ("twilio", "msg91"):
        raise HTTPException(400, f"{data.provider_type} is an SMS provider")
    if data.channel == "sms" and data.provider_type == "smtp":
        raise HTTPException(400, "smtp can only be used for email")
    if data.provider_type == "smtp" and not data.config.get("host"):
        raise HTTPException(400, "smtp provider needs a host in config")
    if db.query(MessageProvider).filter(MessageProvider.name == data.name.strip()).first():
        raise HTTPException(409, "Provider with this name already exists")
    p = MessageProvider(channel=data.channel, name=data.name.strip(), provider_type=data.provider_type,
                        config=to_json(data.config), is_active=data.is_active, created_at=utcnow())
    db.add(p)
    db.flush()
    _only_active(db, p)
    audit(db, user, "message_provider_added", "message_provider", p.id, None,
          {"channel": p.channel, "type": p.provider_type, "active": p.is_active})
    db.commit()
    db.refresh(p)
    return _provider_out(p)


@router.patch("/providers/{provider_id:int}", response_model=ProviderOut)
def update_provider(provider_id: int, data: ProviderUpdate, user: User = Depends(admin_only),
                    db: Session = Depends(get_db)):
    p = db.get(MessageProvider, provider_id)
    if not p:
        raise HTTPException(404, "Provider not found")
    if data.config is not None:
        p.config = to_json(data.config)
    if data.is_active is not None:
        p.is_active = data.is_active
        _only_active(db, p)
    audit(db, user, "message_provider_updated", "message_provider", p.id, None, {"active": p.is_active})
    db.commit()
    db.refresh(p)
    return _provider_out(p)


# ---------------- templates ----------------

def _build(db, data):
    """returns (user, subject, body) for the template from real records"""
    if data.template == "email_verification":
        u = db.get(User, data.reference_id)
        if not u:
            raise HTTPException(404, "User not found")
        if getattr(u, "is_email_verified", False):
            raise HTTPException(400, "Email is already verified")
        token = secrets.token_urlsafe(24)
        db.add(EmailVerification(user_id=u.id, token=token, expires_at=utcnow() + timedelta(hours=24),
                                 created_at=utcnow()))
        return u, "Verify your ShopSphere email", f"Hi {u.full_name}, your verification code is {token}. It is valid for 24 hours."

    order = db.get(Order, data.reference_id)
    if not order:
        raise HTTPException(404, "Order not found")
    u = db.get(User, order.user_id)
    if data.template == "order_confirmation":
        if order.status in ("pending", "cancelled"):
            raise HTTPException(400, f"Order is {order.status}, confirmation can't be sent")
        return u, f"Order {order.order_number} confirmed", (
            f"Hi {u.full_name}, your order {order.order_number} of Rs {money(order.grand_total)} "
            f"({order.payment_method.upper()}) is confirmed.")

    shipment = (db.query(Shipment).filter(Shipment.order_id == order.id).order_by(Shipment.id.desc()).first())
    if not shipment:
        raise HTTPException(400, "Order has no shipment yet")
    if data.template == "shipment_update":
        return u, f"Order {order.order_number} shipped", (
            f"Your order {order.order_number} is {shipment.status.replace('_', ' ')}. "
            f"Tracking number: {shipment.tracking_number or 'will be shared soon'}.")
    if shipment.status not in ("out_for_delivery", "delivered", "exception"):
        raise HTTPException(400, f"Shipment is {shipment.status}, no delivery update to send")
    words = {"out_for_delivery": "is out for delivery today", "delivered": "has been delivered",
             "exception": "could not be delivered, we will try again"}
    return u, f"Delivery update for {order.order_number}", f"Your order {order.order_number} {words[shipment.status]}."


@router.post("/send", response_model=List[MessageOut], status_code=201)
def send_template(data: TemplateSend, user: User = Depends(message_staff), db: Session = Depends(get_db)):
    customer, subject, body = _build(db, data)
    messages = [queue_message(db, "email", customer.email, body, subject=subject, template=data.template,
                              user_id=customer.id)]
    if data.sms:
        if not customer.phone:
            raise HTTPException(400, "Customer has no phone number for SMS")
        messages.append(queue_message(db, "sms", customer.phone, f"{subject}: {body}", template=data.template,
                                      user_id=customer.id))
    messages = [m for m in messages if m]
    db.flush()
    if data.send_now:
        for m in messages:
            send_message(db, m)
    audit(db, user, "message_sent", "outbound_message", messages[0].id if messages else None, None,
          {"template": data.template, "reference": data.reference_id, "sms": data.sms})
    db.commit()
    for m in messages:
        db.refresh(m)
    return messages


# ---------------- tracking + retry ----------------

@router.get("", response_model=List[MessageOut])
def list_messages(status: Optional[str] = None, channel: Optional[str] = None, template: Optional[str] = None,
                  user_id: Optional[int] = None, skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                  user: User = Depends(message_staff), db: Session = Depends(get_db)):
    q = db.query(OutboundMessage)
    if status:
        q = q.filter(OutboundMessage.status == status)
    if channel:
        q = q.filter(OutboundMessage.channel == channel)
    if template:
        q = q.filter(OutboundMessage.template == template)
    if user_id:
        q = q.filter(OutboundMessage.user_id == user_id)
    return q.order_by(OutboundMessage.id.desc()).offset(skip).limit(limit).all()


@router.get("/summary")
def message_summary(user: User = Depends(message_staff), db: Session = Depends(get_db)):
    rows = (db.query(OutboundMessage.channel, OutboundMessage.status, func.count(OutboundMessage.id))
            .group_by(OutboundMessage.channel, OutboundMessage.status).all())
    summary = {}
    for channel, status, count in rows:
        summary.setdefault(channel, {})[status] = count
    return summary


@router.get("/{message_id:int}", response_model=MessageOut)
def get_message(message_id: int, user: User = Depends(message_staff), db: Session = Depends(get_db)):
    m = db.get(OutboundMessage, message_id)
    if not m:
        raise HTTPException(404, "Message not found")
    return m


@router.post("/{message_id:int}/retry", response_model=MessageOut)
def retry_message(message_id: int, user: User = Depends(message_staff), db: Session = Depends(get_db)):
    m = db.query(OutboundMessage).filter(OutboundMessage.id == message_id).with_for_update().first()
    if not m:
        raise HTTPException(404, "Message not found")
    # safety: a sent message is never sent again, and attempts are limited
    if m.status == "sent":
        raise HTTPException(409, "Message was already sent, it will not be sent twice")
    if m.attempts >= m.max_attempts:
        raise HTTPException(400, f"Max {m.max_attempts} attempts reached, message can't be retried")
    current = db.get(MessageProvider, m.provider_id) if m.provider_id else None
    if not current or not current.is_active:
        active = (db.query(MessageProvider)
                  .filter(MessageProvider.channel == m.channel, MessageProvider.is_active == True).first())  # noqa: E712
        m.provider_id = active.id if active else None
    send_message(db, m)
    audit(db, user, "message_retried", "outbound_message", m.id, None,
          {"status": m.status, "attempt": m.attempts, "error": m.last_error})
    db.commit()
    db.refresh(m)
    return m


@router.post("/process-outbox")
def run_outbox(include_failed: bool = True, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    """sends queued messages (and retries failed ones that still have attempts left)"""
    return process_outbox(db, include_failed=include_failed)
