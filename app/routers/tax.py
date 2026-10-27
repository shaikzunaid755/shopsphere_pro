# Module 22: Tax Management
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404, get_current_user, check_owner_or_staff
from app.core.roles import ADMIN, FINANCE_MANAGER, PRODUCT_MANAGER
from app.core.utils import money, from_json
from app.models import TaxRule, TaxHistory, Product, Category, Order, User
from app.schemas.pricing import TaxRuleCreate, TaxRuleUpdate, TaxRuleOut, TaxHistoryOut, TaxCalcRequest
from app.services.pricing_service import tax_rule_for

router = APIRouter(prefix="/tax", tags=["22 Tax Management"])

tax_admins = require_roles(ADMIN, FINANCE_MANAGER)
tax_viewers = require_roles(ADMIN, FINANCE_MANAGER, PRODUCT_MANAGER)

def _dup_check(db, product_id, category_id, skip_id=None):
    # only one active rule per product / per category / one default
    q = db.query(TaxRule).filter(TaxRule.is_active == True)  # noqa
    q = q.filter(TaxRule.product_id == product_id) if product_id else q.filter(TaxRule.product_id.is_(None))
    q = q.filter(TaxRule.category_id == category_id) if category_id else q.filter(TaxRule.category_id.is_(None))
    if skip_id:
        q = q.filter(TaxRule.id != skip_id)
    if q.first():
        raise HTTPException(409, "An active tax rule already exists for this target")


@router.post("/rules", response_model=TaxRuleOut, status_code=201)
def create_rule(data: TaxRuleCreate, db: Session = Depends(get_db), user: User = Depends(tax_admins)):
    if data.product_id:
        get_or_404(db, Product, data.product_id)
    if data.category_id:
        get_or_404(db, Category, data.category_id)
    _dup_check(db, data.product_id, data.category_id)
    rule = TaxRule(**data.model_dump())
    db.add(rule)
    db.flush()
    db.add(TaxHistory(tax_rule_id=rule.id, action="created", new_rate=rule.rate, changed_by=user.id))
    audit(db, user, "tax_rule_created", "tax_rule", rule.id, None, data.model_dump())
    db.commit()
    db.refresh(rule)
    return rule

@router.get("/rules", response_model=List[TaxRuleOut])
def list_rules(db: Session = Depends(get_db), user: User = Depends(tax_viewers)):
    return db.query(TaxRule).order_by(TaxRule.id).all()


@router.put("/rules/{rule_id}", response_model=TaxRuleOut)
def update_rule(rule_id: int, data: TaxRuleUpdate, db: Session = Depends(get_db),
                user: User = Depends(tax_admins)):
    rule = get_or_404(db, TaxRule, rule_id, "Tax rule")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_active") and not rule.is_active:
        # switching it back on, make sure no other active rule took its place
        _dup_check(db, rule.product_id, rule.category_id, skip_id=rule.id)
    if "rate" in changes and changes["rate"] != rule.rate:
        db.add(TaxHistory(tax_rule_id=rule.id, action="rate_changed", old_rate=rule.rate,
                          new_rate=changes["rate"], changed_by=user.id))
    if "is_active" in changes and changes["is_active"] != rule.is_active:
        action = "activated" if changes["is_active"] else "deactivated"
        db.add(TaxHistory(tax_rule_id=rule.id, action=action, old_rate=rule.rate,
                          new_rate=rule.rate, changed_by=user.id))
    old = {k: getattr(rule, k) for k in changes}
    for k, v in changes.items():
        setattr(rule, k, v)
    audit(db, user, "tax_rule_updated", "tax_rule", rule.id, old, changes)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/rules/{rule_id}/history", response_model=List[TaxHistoryOut])
def rule_history(rule_id: int, db: Session = Depends(get_db), user: User = Depends(tax_admins)):
    get_or_404(db, TaxRule, rule_id, "Tax rule")
    return (db.query(TaxHistory).filter(TaxHistory.tax_rule_id == rule_id)
            .order_by(TaxHistory.id.desc()).all())


@router.get("/history", response_model=List[TaxHistoryOut], summary="All tax configuration changes")
def all_history(db: Session = Depends(get_db), user: User = Depends(tax_admins)):
    return db.query(TaxHistory).order_by(TaxHistory.id.desc()).limit(200).all()

@router.post("/calculate", summary="Tax for a product and amount")
def calculate_tax(data: TaxCalcRequest, db: Session = Depends(get_db)):
    product = get_or_404(db, Product, data.product_id)
    rule = tax_rule_for(db, product)    # product > category > default
    rate = rule.rate if rule else 0
    amount = money(data.amount)
    tax = money(amount * money(rate) / 100)
    return {"product_id": product.id, "rule": rule.name if rule else None, "rate": rate,
            "amount": amount, "tax": tax, "total": amount + tax}


@router.get("/orders/{order_id}", summary="Tax breakdown stored on an order")
def order_tax(order_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    order = get_or_404(db, Order, order_id)
    check_owner_or_staff(user, order.user_id)
    items = [{"sku": i.sku, "tax_rate": i.tax_rate, "tax_amount": i.tax_amount} for i in order.items]
    return {"order_number": order.order_number, "tax_total": order.tax_total,
            "breakdown": from_json(order.tax_breakdown) or [], "items": items}