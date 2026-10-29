# Module 36: Return Management
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404, check_owner_or_staff
from app.core.roles import SUPER_ADMIN, CUSTOMER, ADMIN, ORDER_MANAGER, FINANCE_MANAGER
from app.core.utils import make_number, money, utcnow
from app.models import Order, OrderItem, OrderStatusHistory, ReturnRequest, ReturnItem, User
from app.schemas.returns import ReturnCreate, ReturnDecision, ReturnReject, ReturnOut
from app.services import order_service

router = APIRouter(prefix="/returns", tags=["36 Return Management"])

customer_only = require_roles(CUSTOMER)
return_staff = require_roles(ADMIN, ORDER_MANAGER)
finance = require_roles(ADMIN, FINANCE_MANAGER)
STAFF_ROLES = (SUPER_ADMIN, ADMIN, ORDER_MANAGER, FINANCE_MANAGER)

RETURN_WINDOW_DAYS = 7
# these requests still "hold" the quantity, a rejected one gives it back
ACTIVE_STATUSES = ("requested", "approved", "received", "refunded")


def _check_access(user: User, owner_id: int):
    if is_customer(user):
        check_owner_or_staff(user, owner_id)
    elif user.role_name not in STAFF_ROLES:
        raise HTTPException(403, "You don't have permission for this action")


def _delivered_at(db, order):
    row = (db.query(OrderStatusHistory)
           .filter(OrderStatusHistory.order_id == order.id, OrderStatusHistory.to_status == "delivered")
           .order_by(OrderStatusHistory.id.desc()).first())
    return row.created_at if row else None


def _already_returned(db, order_item_id):
    total = (db.query(func.coalesce(func.sum(ReturnItem.quantity), 0))
             .join(ReturnRequest, ReturnRequest.id == ReturnItem.return_id)
             .filter(ReturnItem.order_item_id == order_item_id, ReturnRequest.status.in_(ACTIVE_STATUSES))
             .scalar())
    return int(total or 0)


def check_eligibility(db, order):
    """order level checks + how many of each item can still be returned"""
    problems = []
    delivered = _delivered_at(db, order)
    if order.status != "delivered" or not delivered:
        problems.append(f"Order is '{order.status}', only delivered orders can be returned")
    deadline = delivered + timedelta(days=RETURN_WINDOW_DAYS) if delivered else None
    if deadline and utcnow() > deadline:
        problems.append(f"Return window of {RETURN_WINDOW_DAYS} days closed on {deadline:%d-%m-%Y}")

    items = []
    for it in order.items:
        done = _already_returned(db, it.id)
        can = it.quantity - done if it.fulfillment_status == "delivered" else 0
        items.append({"order_item_id": it.id, "product_name": it.product_name, "ordered": it.quantity,
                      "already_returned": done, "returnable_qty": max(can, 0),
                      "item_status": it.fulfillment_status})
    if not problems and not any(i["returnable_qty"] > 0 for i in items):
        problems.append("All items of this order are already returned")
    return {
        "order_id": order.id,
        "eligible": not problems,
        "problems": problems,
        "delivered_at": delivered,
        "return_deadline": deadline,
        "items": items,
    }


def _locked_return(db, return_id):
    r = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).with_for_update().first()
    if not r:
        raise HTTPException(404, "Return request not found")
    return r


# ---------------- customer side ----------------

@router.get("/orders/{order_id:int}/eligibility")
def return_eligibility(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    _check_access(user, order.user_id)
    return check_eligibility(db, order)


@router.post("", response_model=ReturnOut, status_code=201)
def request_return(data: ReturnCreate, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == data.order_id).with_for_update().first()
    if not order:
        raise HTTPException(404, "Order not found")
    check_owner_or_staff(user, order.user_id)

    info = check_eligibility(db, order)
    if info["problems"]:
        raise HTTPException(400, "; ".join(info["problems"]))
    allowed = {i["order_item_id"]: i for i in info["items"]}
    ids = [i.order_item_id for i in data.items]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "Same item is listed twice, put the total quantity in one line")
    for line in data.items:
        item = allowed.get(line.order_item_id)
        if not item:
            raise HTTPException(400, f"Item {line.order_item_id} is not part of this order")
        if line.quantity > item["returnable_qty"]:
            raise HTTPException(400, f"Only {item['returnable_qty']} of '{item['product_name']}' can be returned")

    ret = ReturnRequest(return_number=make_number("RET"), order_id=order.id, user_id=user.id,
                        reason=data.reason.strip(), status="requested")
    for line in data.items:
        ret.items.append(ReturnItem(order_item_id=line.order_item_id, quantity=line.quantity,
                                    reason=line.reason or data.reason))
    db.add(ret)
    db.flush()
    audit(db, user, "return_requested", "return", ret.id, None,
          {"order": order.id, "items": [(i.order_item_id, i.quantity) for i in data.items]})
    db.commit()
    db.refresh(ret)
    return ret


@router.get("/my", response_model=List[ReturnOut])
def my_returns(status: Optional[str] = None, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    q = db.query(ReturnRequest).filter(ReturnRequest.user_id == user.id)
    if status:
        q = q.filter(ReturnRequest.status == status)
    return q.order_by(ReturnRequest.id.desc()).all()


# ---------------- staff side ----------------

@router.get("", response_model=List[ReturnOut])
def list_returns(status: Optional[str] = None, order_id: Optional[int] = None,
                 date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                 skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                 user: User = Depends(require_roles(ADMIN, ORDER_MANAGER, FINANCE_MANAGER)),
                 db: Session = Depends(get_db)):
    q = db.query(ReturnRequest)
    if status:
        q = q.filter(ReturnRequest.status == status)
    if order_id:
        q = q.filter(ReturnRequest.order_id == order_id)
    if date_from:
        q = q.filter(ReturnRequest.created_at >= date_from)
    if date_to:
        q = q.filter(ReturnRequest.created_at <= date_to)
    return q.order_by(ReturnRequest.id.desc()).offset(skip).limit(limit).all()


@router.get("/{return_id:int}", response_model=ReturnOut)
def get_return(return_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ret = get_or_404(db, ReturnRequest, return_id, "Return request")
    _check_access(user, ret.user_id)
    return ret


@router.post("/{return_id:int}/approve", response_model=ReturnOut)
def approve_return(return_id: int, data: ReturnDecision, user: User = Depends(return_staff),
                   db: Session = Depends(get_db)):
    ret = _locked_return(db, return_id)
    if ret.status != "requested":
        raise HTTPException(400, f"Return is already {ret.status}")
    ret.status = "approved"
    ret.admin_note = data.note or "Approved, please hand over the items to the pickup agent"
    ret.reviewed_by = user.id
    audit(db, user, "return_approved", "return", ret.id, {"status": "requested"}, {"status": "approved"})
    db.commit()
    db.refresh(ret)
    return ret


@router.post("/{return_id:int}/reject", response_model=ReturnOut)
def reject_return(return_id: int, data: ReturnReject, user: User = Depends(return_staff),
                  db: Session = Depends(get_db)):
    ret = _locked_return(db, return_id)
    if ret.status != "requested":
        raise HTTPException(400, f"Return is already {ret.status}")
    ret.status = "rejected"
    ret.admin_note = data.note
    ret.reviewed_by = user.id
    audit(db, user, "return_rejected", "return", ret.id, {"status": "requested"},
          {"status": "rejected", "note": data.note})
    db.commit()
    db.refresh(ret)
    return ret


@router.post("/{return_id:int}/receive", response_model=ReturnOut)
def receive_return(return_id: int, data: ReturnDecision, user: User = Depends(return_staff),
                   db: Session = Depends(get_db)):
    """items reached the warehouse"""
    ret = _locked_return(db, return_id)
    if ret.status != "approved":
        raise HTTPException(400, f"Only approved returns can be received (this one is {ret.status})")
    for line in ret.items:
        item = db.get(OrderItem, line.order_item_id)
        # whole quantity of that line came back -> mark the line returned
        if _already_returned(db, item.id) >= item.quantity:
            item.fulfillment_status = "returned"
    ret.status = "received"
    if data.note:
        ret.admin_note = data.note
    audit(db, user, "return_received", "return", ret.id, {"status": "approved"}, {"status": "received"})
    db.commit()
    db.refresh(ret)
    return ret


@router.post("/{return_id:int}/refund", response_model=ReturnOut)
def refund_return(return_id: int, user: User = Depends(finance), db: Session = Depends(get_db)):
    """money back for the returned items, uses the same refund logic as module 31"""
    ret = _locked_return(db, return_id)
    if ret.status != "received":
        raise HTTPException(400, f"Items must be received before refund (return is {ret.status})")
    order = db.get(Order, ret.order_id)
    amount = Decimal("0")
    for line in ret.items:
        item = db.get(OrderItem, line.order_item_id)
        # line_total already has discount and tax, take the share for the returned qty
        amount += money(item.line_total) / item.quantity * line.quantity
    refund = order_service.create_refund(db, order, money(amount), f"Return {ret.return_number}", user,
                                         process_now=True)
    ret.refund_id = refund.id
    ret.status = "refunded"
    audit(db, user, "return_refunded", "return", ret.id, {"status": "received"},
          {"status": "refunded", "refund": refund.refund_number, "amount": money(amount)})
    db.commit()
    db.refresh(ret)
    return ret
