# Module 21: Promotion Management
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, PRODUCT_MANAGER
from app.core.utils import utcnow, to_json, money
from app.models import Promotion, PromotionHistory, Product, Category, User
from app.schemas.pricing import PromotionCreate, PromotionUpdate, PromotionOut, PromotionHistoryOut, ActiveToggle
from app.services.pricing_service import best_promotion, unit_price

router = APIRouter(prefix="/promotions", tags=["21 Promotion Management"])

managers = require_roles(ADMIN, PRODUCT_MANAGER)


def _log(db, promo_id, action, details, user):
    # every change is kept in promotion_history
    db.add(PromotionHistory(promotion_id=promo_id, action=action, details=to_json(details),
                            changed_by=user.id if user else None))

@router.post("", response_model=PromotionOut, status_code=201)
def create_promotion(data: PromotionCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    if data.product_id:
        get_or_404(db, Product, data.product_id)
    if data.category_id:
        get_or_404(db, Category, data.category_id)
    promo = Promotion(**data.model_dump(), created_by=user.id)
    # keep only the id that matches the type
    if data.promo_type == "product":
        promo.category_id = None
    else:
        promo.product_id = None
    db.add(promo)
    db.flush()
    _log(db, promo.id, "created", data.model_dump(), user)
    audit(db, user, "promotion_created", "promotion", promo.id, None, {"name": promo.name})
    db.commit()
    db.refresh(promo)
    return promo

@router.get("", response_model=List[PromotionOut])
def list_promotions(active_now: bool = False, promo_type: Optional[str] = None,
                    db: Session = Depends(get_db)):
    q = db.query(Promotion)
    if active_now:
        now = utcnow()
        q = q.filter(Promotion.is_active == True, Promotion.start_date <= now,  # noqa
                     Promotion.end_date >= now)
    if promo_type:
        q = q.filter(Promotion.promo_type == promo_type)
    return q.order_by(Promotion.id.desc()).all()


@router.get("/products/{product_id}/best", summary="Best promotion that would apply to a product")
def best_for_product(product_id: int, quantity: int = 1, db: Session = Depends(get_db)):
    p = get_or_404(db, Product, product_id)
    amount = money(unit_price(p) * quantity)
    promo, discount = best_promotion(db, p, quantity, amount, amount)
    return {"product_id": p.id, "quantity": quantity, "amount": amount, "discount": discount,
            "promotion": PromotionOut.model_validate(promo) if promo else None}


@router.get("/{promotion_id}", response_model=PromotionOut)
def get_promotion(promotion_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, Promotion, promotion_id)

@router.put("/{promotion_id}", response_model=PromotionOut)
def update_promotion(promotion_id: int, data: PromotionUpdate, db: Session = Depends(get_db),
                     user: User = Depends(managers)):
    promo = get_or_404(db, Promotion, promotion_id)
    changes = data.model_dump(exclude_unset=True)
    start = changes.get("start_date", promo.start_date)
    end = changes.get("end_date", promo.end_date)
    if end <= start:
        raise HTTPException(400, "end_date must be after start_date")
    if promo.discount_type == "percentage" and (changes.get("discount_value") or 0) > 100:
        raise HTTPException(400, "percentage can't be over 100")
    old = {k: getattr(promo, k) for k in changes}
    for k, v in changes.items():
        setattr(promo, k, v)
    _log(db, promo.id, "updated", {"old": old, "new": changes}, user)
    db.commit()
    db.refresh(promo)
    return promo

@router.patch("/{promotion_id}/status", response_model=PromotionOut)
def promotion_status(promotion_id: int, data: ActiveToggle, db: Session = Depends(get_db),
                     user: User = Depends(managers)):
    promo = get_or_404(db, Promotion, promotion_id)
    promo.is_active = data.is_active
    _log(db, promo.id, "activated" if data.is_active else "deactivated", None, user)
    db.commit()
    db.refresh(promo)
    return promo


@router.get("/{promotion_id}/history", response_model=List[PromotionHistoryOut])
def promotion_history(promotion_id: int, db: Session = Depends(get_db), user: User = Depends(managers)):
    get_or_404(db, Promotion, promotion_id)
    return (db.query(PromotionHistory).filter(PromotionHistory.promotion_id == promotion_id)
            .order_by(PromotionHistory.id.desc()).all())