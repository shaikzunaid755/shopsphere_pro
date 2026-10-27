# Module 20: Coupon Management
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404, get_current_user
from app.core.roles import ADMIN, PRODUCT_MANAGER
from app.core.utils import utcnow
from app.models import Coupon, CouponUsage, User
from app.schemas.pricing import CouponCreate, CouponUpdate, CouponOut, ActiveToggle
from app.services.pricing_service import validate_coupon

router = APIRouter(prefix="/coupons", tags=["20 Coupon Management"])

managers = require_roles(ADMIN, PRODUCT_MANAGER)

@router.post("", response_model=CouponOut, status_code=201)
def create_coupon(data: CouponCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    code = data.code.upper().strip()
    if db.query(Coupon).filter(Coupon.code == code).first():
        raise HTTPException(409, "Coupon code already exists")
    c = Coupon(**data.model_dump(), created_by=user.id)
    c.code = code
    db.add(c)
    db.flush()
    audit(db, user, "coupon_created", "coupon", c.id, None, {"code": code})
    db.commit()
    db.refresh(c)
    return c


@router.get("", response_model=List[CouponOut])
def list_coupons(active: Optional[bool] = None, db: Session = Depends(get_db),
                 user: User = Depends(managers)):
    q = db.query(Coupon)
    if active is not None:
        q = q.filter(Coupon.is_active == active)
    return q.order_by(Coupon.id.desc()).all()

@router.get("/available", response_model=List[CouponOut], summary="Coupons a shopper can use right now")
def available(db: Session = Depends(get_db)):
    now = utcnow()
    rows = (db.query(Coupon)
            .filter(Coupon.is_active == True, Coupon.valid_from <= now, Coupon.valid_to >= now)  # noqa
            .all())
    return [c for c in rows if c.usage_limit is None or c.used_count < c.usage_limit]


@router.get("/validate/{code}", summary="Check a coupon against an amount")
def check_coupon(code: str, amount: Decimal, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    coupon, discount = validate_coupon(db, code, user.id, amount)
    return {"code": coupon.code, "valid": True, "discount": discount,
            "amount_after_discount": amount - discount}


@router.get("/{coupon_id}", response_model=CouponOut)
def get_coupon(coupon_id: int, db: Session = Depends(get_db), user: User = Depends(managers)):
    return get_or_404(db, Coupon, coupon_id)

@router.put("/{coupon_id}", response_model=CouponOut)
def update_coupon(coupon_id: int, data: CouponUpdate, db: Session = Depends(get_db),
                  user: User = Depends(managers)):
    c = get_or_404(db, Coupon, coupon_id)
    changes = data.model_dump(exclude_unset=True)
    # check the final values = new value if sent, else the old one
    dtype = changes.get("discount_type", c.discount_type)
    dval = changes.get("discount_value", c.discount_value)
    if dtype not in ("percentage", "fixed"):
        raise HTTPException(400, "discount_type must be percentage or fixed")
    if dtype == "percentage" and dval > 100:
        raise HTTPException(400, "percentage can't be over 100")
    if changes.get("valid_to", c.valid_to) <= changes.get("valid_from", c.valid_from):
        raise HTTPException(400, "valid_to must be after valid_from")
    limit = changes.get("usage_limit")
    if limit is not None and limit < c.used_count:
        raise HTTPException(400, f"Coupon already used {c.used_count} times")

    old = {k: getattr(c, k) for k in changes}
    for k, v in changes.items():
        setattr(c, k, v)
    audit(db, user, "coupon_updated", "coupon", c.id, old, changes)
    db.commit()
    db.refresh(c)
    return c


@router.patch("/{coupon_id}/status", response_model=CouponOut, summary="Activate / deactivate coupon")
def coupon_status(coupon_id: int, data: ActiveToggle, db: Session = Depends(get_db),
                  user: User = Depends(managers)):
    c = get_or_404(db, Coupon, coupon_id)
    c.is_active = data.is_active
    audit(db, user, "coupon_status", "coupon", c.id, None, {"is_active": data.is_active})
    db.commit()
    db.refresh(c)
    return c


@router.get("/{coupon_id}/usage", summary="Who used this coupon")
def coupon_usage(coupon_id: int, db: Session = Depends(get_db), user: User = Depends(managers)):
    get_or_404(db, Coupon, coupon_id)
    rows = db.query(CouponUsage).filter(CouponUsage.coupon_id == coupon_id).all()
    return [{"user_id": r.user_id, "order_id": r.order_id, "discount": r.discount_amount,
             "used_at": r.created_at} for r in rows]