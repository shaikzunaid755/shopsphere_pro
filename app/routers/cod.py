# Module 32: Cash on Delivery Management
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404, check_owner_or_staff
from app.core.roles import SUPER_ADMIN, CUSTOMER, ADMIN, FINANCE_MANAGER, ORDER_MANAGER, DELIVERY_MANAGER
from app.core.utils import money, utcnow
from app.models import Order, Address, Cart, Product, CODRule, CODCollection, PaymentTransaction, User
from app.schemas.payment import (CODRuleUpdate, CODRuleOut, CODEligibilityCheck, CODCollect, CODFail,
                                 CODReconcile, CODCollectionOut)
from app.services import pricing_service as pricing

router = APIRouter(prefix="/cod", tags=["32 Cash on Delivery Management"])

finance = require_roles(ADMIN, FINANCE_MANAGER)
field_staff = require_roles(ADMIN, ORDER_MANAGER, DELIVERY_MANAGER)   # people who hand over the parcel
viewers = require_roles(ADMIN, FINANCE_MANAGER, ORDER_MANAGER, DELIVERY_MANAGER)
customer_only = require_roles(CUSTOMER)
STAFF_ROLES = (SUPER_ADMIN, ADMIN, FINANCE_MANAGER, ORDER_MANAGER, DELIVERY_MANAGER)


def _rule(db):
    rule = db.query(CODRule).first()
    if not rule:
        # only one settings row, made the first time someone needs it
        rule = CODRule(is_enabled=True, min_order_amount=0, max_order_amount=10000)
        db.add(rule)
        db.flush()
    return rule


def _blocked(rule):
    return [p.strip() for p in (rule.blocked_postal_codes or "").split(",") if p.strip()]


def cod_problems(rule, total, postal_code, products):
    """all the reasons COD is not allowed. empty list means COD is fine"""
    if not rule.is_enabled:
        return ["Cash on delivery is currently disabled"]
    problems = []
    if rule.max_order_amount is not None and total > money(rule.max_order_amount):
        problems.append(f"Order total {total} is above the COD limit of {money(rule.max_order_amount)}")
    if total < money(rule.min_order_amount):
        problems.append(f"Order total {total} is below the COD minimum of {money(rule.min_order_amount)}")
    if postal_code and postal_code in _blocked(rule):
        problems.append(f"COD is not available for pincode {postal_code}")
    for p in products:
        if not p.is_cod_allowed:
            problems.append(f"{p.name} cannot be ordered with COD")
    return problems


def _cod_record(db, order_id, lock=False):
    q = db.query(CODCollection).filter(CODCollection.order_id == order_id)
    if lock:
        q = q.with_for_update()
    cod = q.first()
    if not cod:
        raise HTTPException(404, "No COD record for this order")
    return cod


# ---------- eligibility settings ----------

@router.get("/rules", response_model=CODRuleOut)
def get_rules(user: User = Depends(viewers), db: Session = Depends(get_db)):
    rule = _rule(db)
    db.commit()
    return rule


@router.put("/rules", response_model=CODRuleOut)
def update_rules(data: CODRuleUpdate, user: User = Depends(finance), db: Session = Depends(get_db)):
    rule = _rule(db)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "Nothing to update")

    new_min = money(changes.get("min_order_amount", rule.min_order_amount))
    new_max = money(changes.get("max_order_amount", rule.max_order_amount))
    if new_min > new_max:
        raise HTTPException(400, "Minimum amount can't be more than maximum amount")
    if "blocked_postal_codes" in changes:
        # clean spaces and duplicates, keep the order they were typed
        codes = [c.strip() for c in (changes["blocked_postal_codes"] or "").split(",") if c.strip()]
        changes["blocked_postal_codes"] = ",".join(dict.fromkeys(codes)) or None

    old = {k: getattr(rule, k) for k in changes}
    for key, value in changes.items():
        setattr(rule, key, value)
    rule.updated_by = user.id
    audit(db, user, "cod_rules_updated", "cod_rule", rule.id, old, changes)
    db.commit()
    db.refresh(rule)
    return rule


# ---------- order / delivery restrictions ----------

@router.post("/check-eligibility")
def check_eligibility(data: CODEligibilityCheck, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    """customer asks before checkout: can I pay cash for my current cart at this address?"""
    cart = db.query(Cart).filter(Cart.user_id == user.id).first()
    if not cart or not cart.items:
        raise HTTPException(400, "Your cart is empty")
    addr = db.get(Address, data.shipping_address_id)
    if not addr or addr.user_id != user.id or addr.is_deleted:
        raise HTTPException(400, "Invalid shipping address")

    items = [(ci.product, ci.variant, ci.quantity) for ci in cart.items]
    # same price calculation as checkout, with cod=True so any cod fee is included
    calc = pricing.calculate(db, user.id, items, data.coupon_code or cart.coupon_code, data.shipping_method_id,
                             addr.state, cod=True)
    if calc["errors"]:
        raise HTTPException(409, {"message": "Cart has problems", "errors": calc["errors"]})

    rule = _rule(db)
    problems = cod_problems(rule, calc["grand_total"], addr.postal_code, [p for p, _, _ in items])
    db.commit()
    return {
        "eligible": not problems,
        "reasons": problems,
        "order_total": calc["grand_total"],
        "postal_code": addr.postal_code,
        "cod_limits": {"min": money(rule.min_order_amount), "max": money(rule.max_order_amount)},
    }


@router.get("/orders/{order_id:int}/validate")
def validate_order(order_id: int, user: User = Depends(viewers), db: Session = Depends(get_db)):
    """re-check a placed COD order against today's rules before it goes out for delivery"""
    order = get_or_404(db, Order, order_id, "Order")
    if order.payment_method != "cod":
        raise HTTPException(400, "This is not a COD order")
    addr = db.get(Address, order.shipping_address_id) if order.shipping_address_id else None
    product_ids = {i.product_id for i in order.items if i.fulfillment_status != "cancelled"}
    products = db.query(Product).filter(Product.id.in_(product_ids)).all() if product_ids else []

    problems = cod_problems(_rule(db), money(order.grand_total), addr.postal_code if addr else None, products)
    if order.status in ("cancelled", "delivered"):
        problems.append(f"Order is already {order.status}")
    db.commit()
    return {"order_id": order.id, "order_number": order.order_number, "can_deliver_with_cod": not problems,
            "reasons": problems}


# ---------- collection tracking ----------

@router.get("/collections", response_model=List[CODCollectionOut])
def list_collections(status: Optional[str] = None, date_from: Optional[datetime] = None,
                     date_to: Optional[datetime] = None, skip: int = Query(0, ge=0),
                     limit: int = Query(50, ge=1, le=200), user: User = Depends(viewers),
                     db: Session = Depends(get_db)):
    q = db.query(CODCollection)
    if status:
        q = q.filter(CODCollection.status == status)
    if date_from:
        q = q.filter(CODCollection.created_at >= date_from)
    if date_to:
        q = q.filter(CODCollection.created_at <= date_to)
    return q.order_by(CODCollection.id.desc()).offset(skip).limit(limit).all()


@router.get("/collections/{order_id:int}", response_model=CODCollectionOut)
def get_collection(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    if is_customer(user):
        check_owner_or_staff(user, order.user_id)
    elif user.role_name not in STAFF_ROLES:
        raise HTTPException(403, "You don't have permission for this action")
    return _cod_record(db, order.id)


@router.post("/collections/{order_id:int}/collect", response_model=CODCollectionOut)
def record_collection(order_id: int, data: CODCollect, user: User = Depends(field_staff),
                      db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    if order.payment_method != "cod":
        raise HTTPException(400, "This is not a COD order")
    if order.status not in ("shipped", "delivered"):
        raise HTTPException(400, f"Cash is collected at delivery, order is '{order.status}'")
    cod = _cod_record(db, order.id, lock=True)
    if cod.status != "pending":
        raise HTTPException(400, f"Collection is already {cod.status}")

    amount = money(data.collected_amount)
    expected = money(cod.expected_amount)
    if amount > expected:
        raise HTTPException(400, f"Collected amount is more than expected ({expected})")
    if amount < expected and not data.note:
        raise HTTPException(400, "Less cash than expected, add a note explaining why")

    cod.collected_amount = amount
    cod.status = "collected" if amount == expected else "short_collected"
    cod.collected_by = user.id
    cod.collected_at = utcnow()
    cod.note = data.note
    order.payment_status = "cod_collected"
    db.add(PaymentTransaction(order_id=order.id, event_type="cod_collected", status=cod.status,
                              amount=amount, gateway_reference=f"COD-{order.order_number}"))
    audit(db, user, "cod_collected", "order", order.id, {"status": "pending"},
          {"status": cod.status, "amount": amount})
    db.commit()
    db.refresh(cod)
    return cod


@router.post("/collections/{order_id:int}/fail", response_model=CODCollectionOut)
def collection_failed(order_id: int, data: CODFail, user: User = Depends(field_staff),
                      db: Session = Depends(get_db)):
    """customer refused the parcel / no cash at the door"""
    order = get_or_404(db, Order, order_id, "Order")
    cod = _cod_record(db, order.id, lock=True)
    if cod.status != "pending":
        raise HTTPException(400, f"Collection is already {cod.status}")
    cod.status = "failed"
    cod.note = data.note
    cod.collected_by = user.id
    cod.collected_at = utcnow()
    order.payment_status = "failed"
    db.add(PaymentTransaction(order_id=order.id, event_type="cod_failed", status="failed",
                              amount=cod.expected_amount, gateway_reference=f"COD-{order.order_number}"))
    audit(db, user, "cod_failed", "order", order.id, {"status": "pending"}, {"status": "failed", "note": data.note})
    db.commit()
    db.refresh(cod)
    return cod


# ---------- reconciliation ----------

@router.post("/reconcile")
def reconcile(data: CODReconcile, user: User = Depends(finance), db: Session = Depends(get_db)):
    """match collected cash with order totals. clean ones are closed, mismatches are listed"""
    if data.accept_mismatch and not data.note:
        raise HTTPException(400, "Give a note when accepting mismatches")
    q = db.query(CODCollection).filter(CODCollection.status.in_(["collected", "short_collected"]))
    if data.order_ids:
        q = q.filter(CODCollection.order_id.in_(data.order_ids))
    rows = q.with_for_update().all()

    reconciled, mismatched = [], []
    total = Decimal("0")
    for cod in rows:
        order = db.get(Order, cod.order_id)
        expected, collected = money(cod.expected_amount), money(cod.collected_amount)
        issues = []
        if expected != money(order.grand_total):
            issues.append(f"COD expected {expected} but order total is {money(order.grand_total)}")
        if collected != expected:
            issues.append(f"short by {money(expected - collected)}")
        if order.status == "cancelled":
            issues.append("order is cancelled")

        row = {"order_id": order.id, "order_number": order.order_number, "expected": expected,
               "collected": collected, "issues": issues}
        if issues and not data.accept_mismatch:
            mismatched.append(row)
            continue
        cod.status = "reconciled"
        cod.reconciled_by = user.id
        cod.reconciled_at = utcnow()
        if issues:
            cod.note = f"{cod.note or ''} | accepted: {data.note}".strip(" |")[:255]
        total += collected
        reconciled.append(row)

    audit(db, user, "cod_reconciled", "cod_collection", None, None,
          {"reconciled": len(reconciled), "mismatched": len(mismatched), "amount": total})
    db.commit()
    return {"reconciled_count": len(reconciled), "reconciled_amount": money(total),
            "mismatched_count": len(mismatched), "reconciled": reconciled, "mismatched": mismatched}


@router.get("/reconciliation/summary")
def reconciliation_summary(user: User = Depends(finance), db: Session = Depends(get_db)):
    rows = (db.query(CODCollection.status, func.count(CODCollection.id),
                     func.coalesce(func.sum(CODCollection.expected_amount), 0),
                     func.coalesce(func.sum(CODCollection.collected_amount), 0))
            .group_by(CODCollection.status).all())
    by_status = {s: {"count": c, "expected": money(e), "collected": money(col)} for s, c, e, col in rows}
    short = (db.query(func.coalesce(func.sum(CODCollection.expected_amount - CODCollection.collected_amount), 0))
             .filter(CODCollection.status.in_(["short_collected", "reconciled"]),
                     CODCollection.collected_amount < CODCollection.expected_amount)
             .scalar())

    def amt(status, key):
        return by_status.get(status, {}).get(key, money(0))

    return {
        "by_status": by_status,
        "cash_to_collect": amt("pending", "expected"),
        "collected_not_reconciled": money(amt("collected", "collected") + amt("short_collected", "collected")),
        "reconciled_amount": amt("reconciled", "collected"),
        "total_short": money(short),
    }
