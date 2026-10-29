# Module 29: Payment Management  +  Module 30: Payment Transaction History
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Header, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404, check_owner_or_staff
from app.core.roles import SUPER_ADMIN, CUSTOMER, ADMIN, FINANCE_MANAGER, ORDER_MANAGER
from app.core.utils import money, to_json
from app.models import Order, Payment, PaymentTransaction, WebhookEvent, User
from app.schemas.payment import PaymentCreate, PaymentCreated, PaymentOut, SandboxConfirm, TransactionOut
from app.services import payment_gateway as gateway
from app.services import order_service

router = APIRouter(prefix="/payments", tags=["29 Payment Management"])
history_router = APIRouter(prefix="/payments", tags=["30 Payment Transaction History"])

finance = require_roles(ADMIN, FINANCE_MANAGER)
customer_only = require_roles(CUSTOMER)
STAFF_ROLES = (SUPER_ADMIN, ADMIN, FINANCE_MANAGER, ORDER_MANAGER)


def _check_access(user: User, owner_id: int):
    # customer -> only own records, staff -> only finance / order / admin roles
    if is_customer(user):
        check_owner_or_staff(user, owner_id)
    elif user.role_name not in STAFF_ROLES:
        raise HTTPException(403, "You don't have permission for this action")


def _created_response(p: Payment):
    return PaymentCreated(payment_id=p.id, order_id=p.order_id, amount=p.amount, currency=p.currency,
                          method=p.method, gateway=p.gateway, status=p.status,
                          gateway_reference=p.gateway_reference, client_secret=p.client_secret)


def _apply_event(db, payment, event_type, reason=None, raw=None):
    # one place that changes payment status, used by the webhook and the sandbox confirm
    if event_type == "payment_intent.succeeded":
        order_service.mark_payment_succeeded(db, payment, raw)
    elif event_type == "payment_intent.payment_failed":
        order_service.mark_payment_failed(db, payment, reason, raw)
    elif event_type == "payment_intent.canceled":
        if payment.status == "pending":
            payment.status = "cancelled"
            db.add(PaymentTransaction(payment_id=payment.id, order_id=payment.order_id,
                                      event_type="payment_cancelled", status="cancelled",
                                      amount=payment.amount, gateway_reference=payment.gateway_reference,
                                      raw_payload=raw))
    else:
        # processing / requires_action etc. - payment stays pending, just keep a record
        db.add(PaymentTransaction(payment_id=payment.id, order_id=payment.order_id,
                                  event_type=event_type[:40], status=payment.status,
                                  amount=payment.amount, gateway_reference=payment.gateway_reference,
                                  raw_payload=raw))


# ---------------- Module 29: Payment Management ----------------

@router.post("", response_model=PaymentCreated, status_code=201)
def create_payment(data: PaymentCreate, user: User = Depends(customer_only), db: Session = Depends(get_db),
                   idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    # same key sent again (retry / double click) -> same payment back, nothing new is charged
    if idempotency_key:
        old = db.query(Payment).filter(Payment.idempotency_key == idempotency_key).first()
        if old:
            if old.user_id != user.id:
                raise HTTPException(409, "Idempotency key already used")
            return _created_response(old)

    # lock the order row so two requests at the same time can't both create a payment
    order = db.query(Order).filter(Order.id == data.order_id).with_for_update().first()
    if not order:
        raise HTTPException(404, "Order not found")
    check_owner_or_staff(user, order.user_id)
    if order.payment_method != "online" or data.method == "cod":
        raise HTTPException(400, "This is a cash on delivery order, no online payment needed")
    if order.status != "pending":
        raise HTTPException(400, f"Order is {order.status}, payment not allowed")
    if order.payment_status not in ("pending", "failed"):
        raise HTTPException(400, f"Order payment is already {order.payment_status}")

    # an open attempt already exists -> reuse it instead of making a second one
    open_payment = (db.query(Payment)
                    .filter(Payment.order_id == order.id, Payment.status == "pending")
                    .order_by(Payment.id.desc()).first())
    if open_payment:
        return _created_response(open_payment)

    key = idempotency_key or f"pay-{order.id}-{uuid.uuid4().hex[:12]}"
    amount = money(order.grand_total)
    gw, ref, secret = gateway.create_payment_intent(amount, order.order_number, key)

    payment = Payment(order_id=order.id, user_id=user.id, amount=amount, currency=settings.CURRENCY,
                      method="card", gateway=gw, gateway_reference=ref, client_secret=secret,
                      status="pending", idempotency_key=key)
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        old = db.query(Payment).filter(Payment.idempotency_key == key).first()
        if old:
            return _created_response(old)
        raise HTTPException(409, "Payment already being created, try again")

    db.add(PaymentTransaction(payment_id=payment.id, order_id=order.id, event_type="payment_created",
                              status="pending", amount=amount, gateway_reference=ref))
    order.payment_status = "pending"
    db.commit()
    db.refresh(payment)
    return _created_response(payment)


@router.post("/webhook")
async def payment_webhook(request: Request, db: Session = Depends(get_db),
                          stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature")):
    """gateway calls this. signature is checked first (stripe secret or SANDBOX_WEBHOOK_SECRET)"""
    body = await request.body()
    event = gateway.verify_webhook(body, stripe_signature)
    if not event.get("id") or not event.get("type"):
        raise HTTPException(400, "Event id and type are required")

    # same event delivered twice -> ignore the second one
    if db.query(WebhookEvent).filter(WebhookEvent.event_id == event["id"]).first():
        return {"received": True, "duplicate": True}
    db.add(WebhookEvent(event_id=event["id"], event_type=event["type"]))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"received": True, "duplicate": True}

    payment = None
    if event.get("payment_reference"):
        payment = (db.query(Payment).filter(Payment.gateway_reference == event["payment_reference"])
                   .with_for_update().first())
    if not payment:
        db.commit()  # keep the event id so it is not processed later by mistake
        return {"received": True, "ignored": "no payment for this reference"}

    _apply_event(db, payment, event["type"], event.get("failure_reason"), body.decode("utf-8", "ignore"))
    db.commit()
    return {"received": True, "payment_id": payment.id, "status": payment.status}


@router.post("/{payment_id:int}/sandbox-confirm", response_model=PaymentOut)
def sandbox_confirm(payment_id: int, data: SandboxConfirm, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    # only for local testing without stripe keys - acts like the gateway sending the result
    payment = get_or_404(db, Payment, payment_id, "Payment")
    _check_access(user, payment.user_id)
    if payment.gateway != "sandbox":
        raise HTTPException(400, "Only sandbox payments can be confirmed here, real ones come by webhook")
    event_type = "payment_intent.succeeded" if data.result == "success" else "payment_intent.payment_failed"
    raw = to_json({"source": "sandbox-confirm", "result": data.result, "by": user.id})
    _apply_event(db, payment, event_type, data.failure_reason or "Card declined (sandbox)", raw)
    db.commit()
    db.refresh(payment)
    return payment


@router.get("/my", response_model=List[PaymentOut])
def my_payments(status: Optional[str] = None, skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                user: User = Depends(customer_only), db: Session = Depends(get_db)):
    q = db.query(Payment).filter(Payment.user_id == user.id)
    if status:
        q = q.filter(Payment.status == status)
    return q.order_by(Payment.id.desc()).offset(skip).limit(limit).all()


@router.get("", response_model=List[PaymentOut])
def list_payments(status: Optional[str] = None, gateway_name: Optional[str] = Query(None, alias="gateway"),
                  order_id: Optional[int] = None, user_id: Optional[int] = None,
                  skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                  user: User = Depends(finance), db: Session = Depends(get_db)):
    q = db.query(Payment)
    if status:
        q = q.filter(Payment.status == status)
    if gateway_name:
        q = q.filter(Payment.gateway == gateway_name)
    if order_id:
        q = q.filter(Payment.order_id == order_id)
    if user_id:
        q = q.filter(Payment.user_id == user_id)
    return q.order_by(Payment.id.desc()).offset(skip).limit(limit).all()


@router.get("/{payment_id:int}", response_model=PaymentOut)
def get_payment(payment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payment = get_or_404(db, Payment, payment_id, "Payment")
    _check_access(user, payment.user_id)
    return payment


@router.get("/orders/{order_id:int}", response_model=List[PaymentOut])
def order_payments(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    _check_access(user, order.user_id)
    return db.query(Payment).filter(Payment.order_id == order.id).order_by(Payment.id).all()


# ---------------- Module 30: Payment Transaction History ----------------

def _filter_txns(q, event_type, status, date_from, date_to):
    if event_type:
        q = q.filter(PaymentTransaction.event_type == event_type)
    if status:
        q = q.filter(PaymentTransaction.status == status)
    if date_from:
        q = q.filter(PaymentTransaction.created_at >= date_from)
    if date_to:
        q = q.filter(PaymentTransaction.created_at <= date_to)
    return q


@history_router.get("/transactions/my", response_model=List[TransactionOut])
def my_transactions(event_type: Optional[str] = None, status: Optional[str] = None,
                    date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                    user: User = Depends(customer_only), db: Session = Depends(get_db)):
    q = (db.query(PaymentTransaction)
         .join(Order, Order.id == PaymentTransaction.order_id)
         .filter(Order.user_id == user.id))
    q = _filter_txns(q, event_type, status, date_from, date_to)
    return q.order_by(PaymentTransaction.id.desc()).offset(skip).limit(limit).all()


@history_router.get("/transactions", response_model=List[TransactionOut])
def all_transactions(event_type: Optional[str] = None, status: Optional[str] = None,
                     order_id: Optional[int] = None, payment_id: Optional[int] = None,
                     date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                     skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                     user: User = Depends(finance), db: Session = Depends(get_db)):
    q = _filter_txns(db.query(PaymentTransaction), event_type, status, date_from, date_to)
    if order_id:
        q = q.filter(PaymentTransaction.order_id == order_id)
    if payment_id:
        q = q.filter(PaymentTransaction.payment_id == payment_id)
    return q.order_by(PaymentTransaction.id.desc()).offset(skip).limit(limit).all()


@history_router.get("/transactions/summary")
def transactions_summary(date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                         user: User = Depends(finance), db: Session = Depends(get_db)):
    q = db.query(PaymentTransaction.event_type, func.count(PaymentTransaction.id),
                 func.coalesce(func.sum(PaymentTransaction.amount), 0))
    q = _filter_txns(q, None, None, date_from, date_to)
    rows = q.group_by(PaymentTransaction.event_type).all()
    by_type = {r[0]: {"count": r[1], "amount": money(r[2])} for r in rows}
    received = by_type.get("payment_succeeded", {}).get("amount", money(0))
    refunded = by_type.get("refund_processed", {}).get("amount", money(0))
    return {"by_event_type": by_type, "total_received": received, "total_refunded": refunded,
            "net": money(received - refunded)}


@history_router.get("/{payment_id:int}/transactions", response_model=List[TransactionOut])
def payment_transactions(payment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payment = get_or_404(db, Payment, payment_id, "Payment")
    _check_access(user, payment.user_id)
    return (db.query(PaymentTransaction).filter(PaymentTransaction.payment_id == payment.id)
            .order_by(PaymentTransaction.id).all())


@history_router.get("/orders/{order_id:int}/transactions", response_model=List[TransactionOut])
def order_transactions(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    _check_access(user, order.user_id)
    return (db.query(PaymentTransaction).filter(PaymentTransaction.order_id == order.id)
            .order_by(PaymentTransaction.id).all())
