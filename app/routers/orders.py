# Module 25: Order Management, Module 26: Order Items, Module 27: Order Status
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, check_owner_or_staff, is_customer, get_or_404
from app.core.roles import ADMIN, ORDER_MANAGER, SUPPORT_AGENT, FINANCE_MANAGER, DELIVERY_MANAGER
from app.core.utils import from_json
from app.models import Order, OrderItem, OrderStatusHistory, User
from app.schemas.order import (OrderOut, OrderDetail, OrderList, OrderItemOut, StatusChange, StatusHistoryOut,
                               ItemFulfillmentUpdate, LineTotal)
from app.services import order_service

router = APIRouter(prefix="/orders", tags=["25 Order Management"])
items_router = APIRouter(prefix="/orders", tags=["26 Order Item Management"])
status_router = APIRouter(prefix="/orders", tags=["27 Order Status Management"])

# staff roles that may look at orders
order_staff = [ADMIN, ORDER_MANAGER, SUPPORT_AGENT, FINANCE_MANAGER, DELIVERY_MANAGER]
status_roles = require_roles(ADMIN, ORDER_MANAGER)

def order_detail(order: Order) -> OrderDetail:
    # order + its items + status timeline in one response
    data = OrderOut.model_validate(order).model_dump()
    return OrderDetail(**data,
                       items=[OrderItemOut.model_validate(i) for i in order.items],
                       status_history=[StatusHistoryOut.model_validate(h) for h in order.status_history],
                       tax_breakdown_list=from_json(order.tax_breakdown) or [])


def load_order(db, order_id, user) -> Order:
    order = get_or_404(db, Order, order_id)
    if is_customer(user):
        check_owner_or_staff(user, order.user_id)    # someone else's order -> 404
    elif user.role_name not in order_staff and user.role_name != "super_admin":
        raise HTTPException(403, "Not allowed")
    return order

@router.get("", response_model=OrderList, summary="Search and filter orders (customers see only their own)")
def list_orders(status: Optional[str] = None, payment_status: Optional[str] = None,
                order_number: Optional[str] = None, user_id: Optional[int] = None,
                date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Order)
    if is_customer(user):
        q = q.filter(Order.user_id == user.id)       # user_id filter is ignored for customers
    elif user.role_name not in order_staff and user.role_name != "super_admin":
        raise HTTPException(403, "Not allowed")
    elif user_id:
        q = q.filter(Order.user_id == user_id)
    if status:
        q = q.filter(Order.status == status)
    if payment_status:
        q = q.filter(Order.payment_status == payment_status)
    if order_number:
        q = q.filter(Order.order_number.ilike(f"%{order_number}%"))
    if date_from:
        q = q.filter(Order.created_at >= date_from)
    if date_to:
        q = q.filter(Order.created_at <= date_to)
    total = q.count()
    rows = q.order_by(Order.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": rows}


@router.get("/number/{order_number}", response_model=OrderDetail)
def by_number(order_number: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.order_number == order_number).first()
    if not order:
        raise HTTPException(404, "Order not found")
    return order_detail(load_order(db, order.id, user))

@router.get("/{order_id}", response_model=OrderDetail)
def get_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return order_detail(load_order(db, order_id, user))


@router.get("/{order_id}/history", response_model=List[StatusHistoryOut], summary="Order history timeline")
def order_history(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return load_order(db, order_id, user).status_history


# ---------- Module 26: items ----------
@items_router.get("/{order_id}/items", response_model=List[OrderItemOut])
def order_items(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return load_order(db, order_id, user).items


@items_router.get("/{order_id}/items/{item_id}", response_model=OrderItemOut)
def order_item(order_id: int, item_id: int, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    order = load_order(db, order_id, user)
    item = next((i for i in order.items if i.id == item_id), None)
    if not item:
        raise HTTPException(404, "Item not found in this order")
    return item

@items_router.get("/{order_id}/line-totals", response_model=List[LineTotal],
                  summary="Line totals = qty x price - discount + tax")
def line_totals(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = load_order(db, order_id, user)
    return [LineTotal(item_id=i.id, quantity=i.quantity, unit_price=i.unit_price,
                      discount_amount=i.discount_amount, tax_amount=i.tax_amount, line_total=i.line_total)
            for i in order.items]


@items_router.patch("/{order_id}/items/{item_id}/fulfillment", response_model=OrderItemOut,
                    summary="Update item level fulfillment status")
def item_fulfillment(order_id: int, item_id: int, data: ItemFulfillmentUpdate, db: Session = Depends(get_db),
                     user: User = Depends(require_roles(ADMIN, ORDER_MANAGER, DELIVERY_MANAGER))):
    order = get_or_404(db, Order, order_id)
    item = db.get(OrderItem, item_id)
    if not item or item.order_id != order.id:        raise HTTPException(404, "Item not found in this order")
    if item.fulfillment_status in ("cancelled", "returned", "exchanged"):
        raise HTTPException(400, f"Item is {item.fulfillment_status}")

    flow = ["pending", "packed", "shipped", "delivered"]
    if flow.index(data.fulfillment_status) < flow.index(item.fulfillment_status):
        raise HTTPException(400, "Item status can't go backwards")
    # shipping single items moves stock, so only allow it through the order status flow
    if data.fulfillment_status in ("shipped", "delivered") and order.status not in ("shipped", "delivered"):
        raise HTTPException(400, "Ship the order first (order status -> shipped)")
    old = item.fulfillment_status
    item.fulfillment_status = data.fulfillment_status
    audit(db, user, "item_fulfillment", "order_item", item.id, {"status": old},
          {"status": data.fulfillment_status})
    db.commit()
    db.refresh(item)
    return item

# ---------- Module 27: status ----------
@status_router.get("/statuses/flow", summary="Allowed status transitions")
def status_flow():
    return order_service.ALLOWED_TRANSITIONS


@status_router.patch("/{order_id}/status", response_model=OrderDetail)
def update_status(order_id: int, data: StatusChange, db: Session = Depends(get_db),
                  user: User = Depends(status_roles)):
    order = get_or_404(db, Order, order_id)
    if data.status == "cancelled":
        raise HTTPException(400, "Use the cancellation endpoint so refunds and reasons are recorded")
    order_service.change_status(db, order, data.status, user, data.note)
    db.commit()
    db.refresh(order)
    return order_detail(order)


@status_router.get("/{order_id}/status-history", response_model=List[StatusHistoryOut])
def status_history(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    load_order(db, order_id, user)
    return (db.query(OrderStatusHistory).filter(OrderStatusHistory.order_id == order_id)
            .order_by(OrderStatusHistory.id).all())
