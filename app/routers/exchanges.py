# Module 37: Exchange Management
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404, check_owner_or_staff
from app.core.roles import SUPER_ADMIN, CUSTOMER, ADMIN, ORDER_MANAGER, DELIVERY_MANAGER
from app.core.utils import make_number, money, utcnow
from app.models import Order, OrderItem, Product, ProductVariant, Exchange, ExchangeHistory, User
from app.routers.returns import RETURN_WINDOW_DAYS, _delivered_at, _already_returned
from app.schemas.exchanges import ExchangeCreate, ExchangeNote, ExchangeReject, ItemReceived, ExchangeOut
from app.services import inventory_service as inv

router = APIRouter(prefix="/exchanges", tags=["37 Exchange Management"])

customer_only = require_roles(CUSTOMER)
exchange_staff = require_roles(ADMIN, ORDER_MANAGER)
dispatch_staff = require_roles(ADMIN, ORDER_MANAGER, DELIVERY_MANAGER)
STAFF_ROLES = (SUPER_ADMIN, ADMIN, ORDER_MANAGER, DELIVERY_MANAGER)
ACTIVE = ("requested", "approved", "item_received", "replacement_shipped", "completed")


def _check_access(user: User, owner_id: int):
    if is_customer(user):
        check_owner_or_staff(user, owner_id)
    elif user.role_name not in STAFF_ROLES:
        raise HTTPException(403, "You don't have permission for this action")


def _move(db, ex, new_status, user, note=None):
    """every status change is written to exchange_history"""
    db.add(ExchangeHistory(exchange_id=ex.id, from_status=ex.status, to_status=new_status, note=note,
                           changed_by=user.id))
    old = ex.status
    ex.status = new_status
    audit(db, user, f"exchange_{new_status}", "exchange", ex.id, {"status": old}, {"status": new_status})


def _locked(db, exchange_id):
    ex = db.query(Exchange).filter(Exchange.id == exchange_id).with_for_update().first()
    if not ex:
        raise HTTPException(404, "Exchange not found")
    return ex


def _list_price(product, variant):
    # catalog price, variant price wins when it is set
    if variant and variant.price is not None:
        return money(variant.price)
    return money(product.base_price)


def _replacement(db, product_id, variant_id):
    product = db.get(Product, product_id)
    if not product or product.status != "active":
        raise HTTPException(400, "Replacement product is not available")
    variant = None
    if variant_id:
        variant = db.get(ProductVariant, variant_id)
        if not variant or variant.product_id != product.id or variant.status != "active":
            raise HTTPException(400, "Replacement variant is not available for this product")
    return product, variant


def _already_exchanged(db, order_item_id):
    total = (db.query(func.coalesce(func.sum(Exchange.quantity), 0))
             .filter(Exchange.order_item_id == order_item_id, Exchange.status.in_(ACTIVE)).scalar())
    return int(total or 0)


# ---------------- stock check ----------------

@router.get("/check-stock")
def check_stock(product_id: int, variant_id: Optional[int] = None, quantity: int = Query(1, ge=1, le=100),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    product, variant = _replacement(db, product_id, variant_id)
    available = inv.total_available(db, product.id, variant.id if variant else None)
    return {"product_id": product.id, "variant_id": variant.id if variant else None,
            "product_name": product.name, "variant_name": variant.name if variant else None,
            "price": _list_price(product, variant), "available": available, "enough_stock": available >= quantity}


# ---------------- customer side ----------------

@router.post("", response_model=ExchangeOut, status_code=201)
def request_exchange(data: ExchangeCreate, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == data.order_id).with_for_update().first()
    if not order:
        raise HTTPException(404, "Order not found")
    check_owner_or_staff(user, order.user_id)

    # same rules as returns: delivered order, inside the return window
    delivered = _delivered_at(db, order)
    if order.status != "delivered" or not delivered:
        raise HTTPException(400, f"Order is '{order.status}', only delivered orders can be exchanged")
    if utcnow() > delivered + timedelta(days=RETURN_WINDOW_DAYS):
        raise HTTPException(400, f"Exchange window of {RETURN_WINDOW_DAYS} days is over")

    item = db.get(OrderItem, data.order_item_id)
    if not item or item.order_id != order.id:
        raise HTTPException(400, "This item is not part of the order")
    if item.fulfillment_status != "delivered":
        raise HTTPException(400, f"Item is '{item.fulfillment_status}', it can't be exchanged")
    left = item.quantity - _already_returned(db, item.id) - _already_exchanged(db, item.id)
    if data.quantity > left:
        raise HTTPException(400, f"Only {max(left, 0)} of '{item.product_name}' can be exchanged")

    product, variant = _replacement(db, data.replacement_product_id, data.replacement_variant_id)
    old_product = db.get(Product, item.product_id)
    old_variant = db.get(ProductVariant, item.variant_id) if item.variant_id else None
    if _list_price(product, variant) != _list_price(old_product, old_variant):
        raise HTTPException(400, "Replacement price is different, please return the item and place a new order")
    available = inv.total_available(db, product.id, variant.id if variant else None)
    if available < data.quantity:
        raise HTTPException(409, f"Only {available} in stock for the replacement")

    ex = Exchange(exchange_number=make_number("EXC"), order_id=order.id, order_item_id=item.id, user_id=user.id,
                  quantity=data.quantity, replacement_product_id=product.id,
                  replacement_variant_id=variant.id if variant else None, reason=data.reason.strip(),
                  status="requested")
    db.add(ex)
    db.flush()
    db.add(ExchangeHistory(exchange_id=ex.id, from_status=None, to_status="requested", note=data.reason,
                           changed_by=user.id))
    audit(db, user, "exchange_requested", "exchange", ex.id, None, {"order_item": item.id, "qty": data.quantity})
    db.commit()
    db.refresh(ex)
    return ex


@router.get("/my", response_model=List[ExchangeOut])
def my_exchanges(status: Optional[str] = None, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    q = db.query(Exchange).filter(Exchange.user_id == user.id)
    if status:
        q = q.filter(Exchange.status == status)
    return q.order_by(Exchange.id.desc()).all()


# ---------------- staff side ----------------

@router.get("", response_model=List[ExchangeOut])
def list_exchanges(status: Optional[str] = None, order_id: Optional[int] = None,
                   date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                   skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                   user: User = Depends(dispatch_staff), db: Session = Depends(get_db)):
    q = db.query(Exchange)
    if status:
        q = q.filter(Exchange.status == status)
    if order_id:
        q = q.filter(Exchange.order_id == order_id)
    if date_from:
        q = q.filter(Exchange.created_at >= date_from)
    if date_to:
        q = q.filter(Exchange.created_at <= date_to)
    return q.order_by(Exchange.id.desc()).offset(skip).limit(limit).all()


@router.get("/{exchange_id:int}", response_model=ExchangeOut)
def get_exchange(exchange_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ex = get_or_404(db, Exchange, exchange_id, "Exchange")
    _check_access(user, ex.user_id)
    return ex


@router.get("/{exchange_id:int}/history")
def exchange_history(exchange_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ex = get_or_404(db, Exchange, exchange_id, "Exchange")
    _check_access(user, ex.user_id)
    item = db.get(OrderItem, ex.order_item_id)
    product = db.get(Product, ex.replacement_product_id)
    variant = db.get(ProductVariant, ex.replacement_variant_id) if ex.replacement_variant_id else None
    return {
        "exchange_number": ex.exchange_number,
        "current_status": ex.status,
        "returned_item": {"order_item_id": item.id, "product_name": item.product_name,
                          "variant_name": item.variant_name, "quantity": ex.quantity},
        "replacement_item": {"product_id": product.id, "product_name": product.name,
                             "variant_name": variant.name if variant else None, "quantity": ex.quantity,
                             "warehouse_id": ex.warehouse_id},
        "history": [{"from": h.from_status, "to": h.to_status, "note": h.note, "by": h.changed_by,
                     "at": h.created_at} for h in ex.history],
    }


@router.post("/{exchange_id:int}/approve", response_model=ExchangeOut)
def approve_exchange(exchange_id: int, data: ExchangeNote, user: User = Depends(exchange_staff),
                     db: Session = Depends(get_db)):
    ex = _locked(db, exchange_id)
    if ex.status != "requested":
        raise HTTPException(400, f"Exchange is already {ex.status}")
    # stock may have changed since the request, check again and hold it now
    available = inv.total_available(db, ex.replacement_product_id, ex.replacement_variant_id)
    if available < ex.quantity:
        raise HTTPException(409, f"Replacement is out of stock now (available {available})")
    ex.warehouse_id = inv.reserve(db, ex.replacement_product_id, ex.replacement_variant_id, ex.quantity,
                                  "exchange", ex.id)
    _move(db, ex, "approved", user, data.note or "Approved, replacement stock reserved")
    db.commit()
    db.refresh(ex)
    return ex


@router.post("/{exchange_id:int}/reject", response_model=ExchangeOut)
def reject_exchange(exchange_id: int, data: ExchangeReject, user: User = Depends(exchange_staff),
                    db: Session = Depends(get_db)):
    ex = _locked(db, exchange_id)
    if ex.status != "requested":
        raise HTTPException(400, f"Exchange is already {ex.status}")
    _move(db, ex, "rejected", user, data.note)
    db.commit()
    db.refresh(ex)
    return ex


@router.post("/{exchange_id:int}/item-received", response_model=ExchangeOut)
def item_received(exchange_id: int, data: ItemReceived, user: User = Depends(dispatch_staff),
                  db: Session = Depends(get_db)):
    """customer's item reached our warehouse"""
    ex = _locked(db, exchange_id)
    if ex.status != "approved":
        raise HTTPException(400, f"Exchange must be approved first (it is {ex.status})")
    item = db.get(OrderItem, ex.order_item_id)
    if data.restock and item.warehouse_id:
        # good condition -> back on the shelf of the warehouse it came from
        row = inv.get_row(db, item.product_id, item.variant_id, item.warehouse_id, create=True, lock=True)
        inv.add_stock(db, row, ex.quantity, mtype="return", ref_type="exchange", ref_id=ex.id,
                      note=f"Returned in {ex.exchange_number}", user=user)
    if _already_returned(db, item.id) + _already_exchanged(db, item.id) >= item.quantity:
        item.fulfillment_status = "exchanged"
    note = data.note or ("Item received, put back in stock" if data.restock else "Item received, not restocked")
    _move(db, ex, "item_received", user, note)
    db.commit()
    db.refresh(ex)
    return ex


@router.post("/{exchange_id:int}/ship-replacement", response_model=ExchangeOut)
def ship_replacement(exchange_id: int, data: ExchangeNote, user: User = Depends(dispatch_staff),
                     db: Session = Depends(get_db)):
    ex = _locked(db, exchange_id)
    if ex.status != "item_received":
        raise HTTPException(400, f"Old item must be received before shipping the replacement (it is {ex.status})")
    # reserved units now really leave the warehouse
    inv.commit_sale(db, ex.replacement_product_id, ex.replacement_variant_id, ex.warehouse_id, ex.quantity,
                    "exchange", ex.id)
    _move(db, ex, "replacement_shipped", user, data.note or "Replacement shipped")
    db.commit()
    db.refresh(ex)
    return ex


@router.post("/{exchange_id:int}/complete", response_model=ExchangeOut)
def complete_exchange(exchange_id: int, data: ExchangeNote, user: User = Depends(dispatch_staff),
                      db: Session = Depends(get_db)):
    ex = _locked(db, exchange_id)
    if ex.status != "replacement_shipped":
        raise HTTPException(400, f"Replacement is not shipped yet (exchange is {ex.status})")
    _move(db, ex, "completed", user, data.note or "Customer received the replacement")
    db.commit()
    db.refresh(ex)
    return ex
