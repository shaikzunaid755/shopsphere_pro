# Module 23: Shipping Configuration
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, DELIVERY_MANAGER
from app.core.utils import money
from app.models import ShippingMethod, User
from app.schemas.pricing import (ShippingMethodCreate, ShippingMethodUpdate, ShippingMethodOut, ActiveToggle,
                                 ShippingQuoteRequest, ShippingQuote)
from app.services.pricing_service import is_method_eligible, shipping_charge

router = APIRouter(prefix="/shipping-methods", tags=["23 Shipping Configuration"])

ship_admins = require_roles(ADMIN, DELIVERY_MANAGER)

@router.post("", response_model=ShippingMethodOut, status_code=201)
def create_method(data: ShippingMethodCreate, db: Session = Depends(get_db),
                  user: User = Depends(ship_admins)):
    code = data.code.upper()
    if db.query(ShippingMethod).filter(ShippingMethod.code == code).first():
        raise HTTPException(409, "Shipping code already exists")
    if data.max_order_amount is not None and data.max_order_amount <= data.min_order_amount:
        raise HTTPException(400, "max_order_amount must be bigger than min_order_amount")
    m = ShippingMethod(**data.model_dump())
    m.code = code
    db.add(m)
    db.flush()
    audit(db, user, "shipping_method_created", "shipping_method", m.id, None, {"code": code})
    db.commit()
    db.refresh(m)
    return m


@router.get("", response_model=List[ShippingMethodOut])
def list_methods(active_only: bool = True, db: Session = Depends(get_db)):
    q = db.query(ShippingMethod)
    if active_only:
        q = q.filter(ShippingMethod.is_active == True)  # noqa
    return q.all()

@router.get("/{method_id}", response_model=ShippingMethodOut)
def get_method(method_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, ShippingMethod, method_id, "Shipping method")


@router.put("/{method_id}", response_model=ShippingMethodOut)
def update_method(method_id: int, data: ShippingMethodUpdate, db: Session = Depends(get_db),
                  user: User = Depends(ship_admins)):
    m = get_or_404(db, ShippingMethod, method_id, "Shipping method")
    changes = data.model_dump(exclude_unset=True)
    old = {k: getattr(m, k) for k in changes}
    for k, v in changes.items():
        setattr(m, k, v)
    audit(db, user, "shipping_method_updated", "shipping_method", m.id, old, changes)
    db.commit()
    db.refresh(m)
    return m


@router.patch("/{method_id}/status", response_model=ShippingMethodOut)
def method_status(method_id: int, data: ActiveToggle, db: Session = Depends(get_db),
                  user: User = Depends(ship_admins)):
    m = get_or_404(db, ShippingMethod, method_id, "Shipping method")
    m.is_active = data.is_active
    audit(db, user, "shipping_method_status", "shipping_method", m.id, None, {"is_active": data.is_active})
    db.commit()
    db.refresh(m)
    return m

@router.post("/quote", response_model=List[ShippingQuote], summary="Shipping cost for every eligible method")
def quote(data: ShippingQuoteRequest, db: Session = Depends(get_db)):
    amount = money(data.order_amount)
    state = None if data.state in (None, "", "string") else data.state   # "string" = swagger example
    out = []
    for m in db.query(ShippingMethod).filter(ShippingMethod.is_active == True).all():  # noqa
        ok, _ = is_method_eligible(m, amount, state, data.cod)
        if ok:
            out.append(ShippingQuote(method_id=m.id, name=m.name, estimated_days=m.estimated_days,
                                     charge=shipping_charge(m, amount, data.item_count)))
    if not out:
        raise HTTPException(400, "No shipping method available for this order")
    return sorted(out, key=lambda q: q.charge)    # cheapest first