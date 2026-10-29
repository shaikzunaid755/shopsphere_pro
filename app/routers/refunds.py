# Module 31: Refund Management
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404, check_owner_or_staff
from app.core.roles import SUPER_ADMIN, CUSTOMER, ADMIN, FINANCE_MANAGER, ORDER_MANAGER
from app.core.utils import money, utcnow
from app.models import Order, PaymentTransaction, Refund, User
from app.schemas.payment import RefundCreate, RefundReject, RefundOut, TransactionOut
from app.services import order_service
from app.services.notification_service import notify_customer

router = APIRouter(prefix="/refunds", tags=["31 Refund Management"])

finance = require_roles(ADMIN, FINANCE_MANAGER)
customer_only = require_roles(CUSTOMER)
# who can look at refunds / raise a request for a customer
STAFF_ROLES = (SUPER_ADMIN, ADMIN, FINANCE_MANAGER, ORDER_MANAGER)
# who can actually send money back
PAYOUT_ROLES = (SUPER_ADMIN, ADMIN, FINANCE_MANAGER)


def _check_access(user: User, owner_id: int):
    if is_customer(user):
        check_owner_or_staff(user, owner_id)
    elif user.role_name not in STAFF_ROLES:
        raise HTTPException(403, "You don't have permission for this action")


def _order_for(db, refund):
    return db.get(Order, refund.order_id)


@router.get("/orders/{order_id:int}/refundable")
def refundable_amount(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """how much of this order can still be refunded"""
    order = get_or_404(db, Order, order_id, "Order")
    _check_access(user, order.user_id)
    refunds = db.query(Refund).filter(Refund.order_id == order.id).all()
    processed = sum((money(r.amount) for r in refunds if r.status == "processed"), Decimal("0"))
    waiting = sum((money(r.amount) for r in refunds if r.status == "requested"), Decimal("0"))
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "grand_total": money(order.grand_total),
        "payment_status": order.payment_status,
        "already_refunded": money(processed),
        "waiting_for_approval": money(waiting),
        "refundable_now": order_service.refundable_amount(db, order),
    }


@router.post("", response_model=RefundOut, status_code=201)
def create_refund(data: RefundCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == data.order_id).with_for_update().first()
    if not order:
        raise HTTPException(404, "Order not found")
    _check_access(user, order.user_id)

    if order.payment_status not in ("paid", "partially_refunded", "cod_collected"):
        raise HTTPException(400, f"Nothing to refund, order payment is '{order.payment_status}'")
    if is_customer(user):
        # before delivery the customer should cancel the order, that refunds automatically
        if order.status != "delivered":
            raise HTTPException(400, "Refund can be requested after delivery. Cancel the order instead")
        if data.process_now:
            raise HTTPException(403, "Only finance team can process refunds")
    elif data.process_now and user.role_name not in PAYOUT_ROLES:
        raise HTTPException(403, "Only finance team can process refunds")

    # amount checks (more than paid, zero etc.) are inside create_refund
    refund = order_service.create_refund(db, order, data.amount, data.reason, user, process_now=data.process_now)
    db.commit()
    db.refresh(refund)
    return refund


@router.post("/{refund_id:int}/process", response_model=RefundOut)
def process_refund(refund_id: int, user: User = Depends(finance), db: Session = Depends(get_db)):
    refund = db.query(Refund).filter(Refund.id == refund_id).with_for_update().first()
    if not refund:
        raise HTTPException(404, "Refund not found")
    # gateway error -> exception, nothing is saved and the refund stays 'requested'
    order_service.process_refund(db, refund, user)
    db.commit()
    db.refresh(refund)
    return refund


@router.post("/{refund_id:int}/reject", response_model=RefundOut)
def reject_refund(refund_id: int, data: RefundReject, user: User = Depends(finance), db: Session = Depends(get_db)):
    refund = db.query(Refund).filter(Refund.id == refund_id).with_for_update().first()
    if not refund:
        raise HTTPException(404, "Refund not found")
    if refund.status != "requested":
        raise HTTPException(400, f"Refund is already {refund.status}")

    refund.status = "rejected"
    refund.note = data.note
    refund.processed_by = user.id
    refund.processed_at = utcnow()
    audit(db, user, "refund_rejected", "refund", refund.id, {"status": "requested"},
          {"status": "rejected", "note": data.note})

    order = _order_for(db, refund)
    customer = db.get(User, order.user_id)
    notify_customer(db, customer, "Refund request rejected",
                    f"Refund {refund.refund_number} for order {order.order_number} was rejected: {data.note}",
                    "refund", "refund", refund.id, template="refund")
    db.commit()
    db.refresh(refund)
    return refund


@router.get("/my", response_model=List[RefundOut])
def my_refunds(status: Optional[str] = None, skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
               user: User = Depends(customer_only), db: Session = Depends(get_db)):
    q = db.query(Refund).join(Order, Order.id == Refund.order_id).filter(Order.user_id == user.id)
    if status:
        q = q.filter(Refund.status == status)
    return q.order_by(Refund.id.desc()).offset(skip).limit(limit).all()


@router.get("", response_model=List[RefundOut])
def list_refunds(status: Optional[str] = None, refund_type: Optional[str] = None, order_id: Optional[int] = None,
                 date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                 skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                 user: User = Depends(require_roles(ADMIN, FINANCE_MANAGER, ORDER_MANAGER)),
                 db: Session = Depends(get_db)):
    q = db.query(Refund)
    if status:
        q = q.filter(Refund.status == status)
    if refund_type:
        q = q.filter(Refund.refund_type == refund_type)
    if order_id:
        q = q.filter(Refund.order_id == order_id)
    if date_from:
        q = q.filter(Refund.created_at >= date_from)
    if date_to:
        q = q.filter(Refund.created_at <= date_to)
    return q.order_by(Refund.id.desc()).offset(skip).limit(limit).all()


@router.get("/{refund_id:int}", response_model=RefundOut)
def get_refund(refund_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    refund = get_or_404(db, Refund, refund_id, "Refund")
    _check_access(user, _order_for(db, refund).user_id)
    return refund


@router.get("/orders/{order_id:int}/history")
def order_refund_history(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """all refunds of an order + the money movements the gateway made for them"""
    order = get_or_404(db, Order, order_id, "Order")
    _check_access(user, order.user_id)
    refunds = db.query(Refund).filter(Refund.order_id == order.id).order_by(Refund.id).all()
    moves = (db.query(PaymentTransaction)
             .filter(PaymentTransaction.order_id == order.id,
                     PaymentTransaction.event_type == "refund_processed")
             .order_by(PaymentTransaction.id).all())
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "payment_status": order.payment_status,
        "refundable_now": order_service.refundable_amount(db, order),
        "refunds": [RefundOut.model_validate(r) for r in refunds],
        "gateway_transactions": [TransactionOut.model_validate(t) for t in moves],
    }
