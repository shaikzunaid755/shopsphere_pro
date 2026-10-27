# Module 28: Order Cancellation
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404
from app.core.roles import ADMIN, ORDER_MANAGER
from app.models import Order, OrderCancellation, User
from app.schemas.order import CancelRequest, CancellationOut
from app.services import order_service

router = APIRouter(prefix="/cancellations", tags=["28 Order Cancellation"])


def _my_order(db, order_id, user):
    order = get_or_404(db, Order, order_id)
    if is_customer(user) and order.user_id != user.id:
        raise HTTPException(404, "Order not found")
    return order


@router.get("/orders/{order_id}/eligibility")
def eligibility(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = _my_order(db, order_id, user)
    allowed = ["pending", "confirmed"] if is_customer(user) else ["pending", "confirmed", "processing"]
    can = order.status in allowed
    return {"order_id": order.id, "status": order.status, "can_cancel": can,
            "refund_on_cancel": order_service.refundable_amount(db, order) if can else 0,
            "reason": None if can else f"Orders in '{order.status}' state can't be cancelled"}

@router.post("/orders/{order_id}", response_model=CancellationOut, status_code=201,
             summary="Cancel order: releases stock and refunds if already paid")
def cancel(order_id: int, data: CancelRequest, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    order = _my_order(db, order_id, user)
    staff = user.role_name in (ADMIN, ORDER_MANAGER, "super_admin")
    if not is_customer(user) and not staff:
        raise HTTPException(403, "Only order managers can cancel orders")
    try:
        return order_service.cancel_order(db, order, user, data.reason, is_staff=staff)
    except HTTPException:
        db.rollback()
        raise


@router.get("", response_model=List[CancellationOut])
def list_cancellations(db: Session = Depends(get_db), user: User = Depends(require_roles(ADMIN, ORDER_MANAGER))):
    return db.query(OrderCancellation).order_by(OrderCancellation.id.desc()).all()


@router.get("/orders/{order_id}", response_model=CancellationOut)
def get_cancellation(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _my_order(db, order_id, user)
    c = db.query(OrderCancellation).filter(OrderCancellation.order_id == order_id).first()
    if not c:
        raise