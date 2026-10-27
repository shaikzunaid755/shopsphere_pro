# Module 12: Product Pricing
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, PRODUCT_MANAGER
from app.core.utils import utcnow, money
from app.models import Product, ProductVariant, PriceHistory, User
from app.schemas.catalog import (PriceUpdate, VariantPriceUpdate, PriceHistoryOut, CurrentPriceOut,
                                 ProductOut, VariantOut)

router = APIRouter(prefix="/pricing", tags=["12 Product Pricing"])

# only these roles may touch prices
price_managers = require_roles(ADMIN, PRODUCT_MANAGER)

MAX_CHANGE_PERCENT = Decimal("70")  # bigger jumps need a reason, avoids typo like 999 -> 9


def _current(p: Product):
    now = utcnow()
    active = p.sale_price is not None and (p.sale_start is None or p.sale_start <= now) \
        and (p.sale_end is None or p.sale_end >= now)
    return CurrentPriceOut(product_id=p.id, base_price=p.base_price, sale_price=p.sale_price, sale_active=active,
                           current_price=p.current_price(), sale_start=p.sale_start, sale_end=p.sale_end)


@router.get("/products/{product_id}", response_model=CurrentPriceOut)
def current_price(product_id: int, db: Session = Depends(get_db)):
    return _current(get_or_404(db, Product, product_id))


@router.put("/products/{product_id}", response_model=ProductOut, summary="Change base / sale price")
def update_price(product_id: int, data: PriceUpdate, db: Session = Depends(get_db),
                 user: User = Depends(price_managers)):
    p = get_or_404(db, Product, product_id)
    new_base = data.base_price if data.base_price is not None else p.base_price
    new_sale = None if data.remove_sale else (data.sale_price if data.sale_price is not None else p.sale_price)
    new_start = None if data.remove_sale else (data.sale_start or p.sale_start)
    new_end = None if data.remove_sale else (data.sale_end or p.sale_end)

    if new_sale is not None and new_sale >= new_base:
        raise HTTPException(400, "Sale price must be lower than base price")
    if new_start and new_end and new_end <= new_start:
        raise HTTPException(400, "Sale end must be after sale start")
    if data.base_price is not None:
        change = abs(money(new_base) - money(p.base_price)) / money(p.base_price) * 100
        if change > MAX_CHANGE_PERCENT and not data.reason:
            raise HTTPException(400, f"Price change of {change:.0f}% needs a reason")
    hist = PriceHistory(product_id=p.id, old_base_price=p.base_price, new_base_price=new_base,
                        old_sale_price=p.sale_price, new_sale_price=new_sale, valid_from=new_start,
                        valid_to=new_end, reason=data.reason, changed_by=user.id)
    db.add(hist)
    old = {"base_price": p.base_price, "sale_price": p.sale_price}
    p.base_price, p.sale_price, p.sale_start, p.sale_end = new_base, new_sale, new_start, new_end
    audit(db, user, "price_changed", "product", p.id, old, {"base_price": new_base, "sale_price": new_sale})
    db.commit()
    db.refresh(p)
    return p


@router.put("/variants/{variant_id}", response_model=VariantOut, summary="Set variant specific price")
def update_variant_price(variant_id: int, data: VariantPriceUpdate, db: Session = Depends(get_db),
                         user: User = Depends(price_managers)):
    v = get_or_404(db, ProductVariant, variant_id, "Variant")
    db.add(PriceHistory(product_id=v.product_id, variant_id=v.id, old_base_price=v.price,
                        new_base_price=data.price, reason=data.reason, changed_by=user.id))
    audit(db, user, "variant_price_changed", "variant", v.id, {"price": v.price}, {"price": data.price})
    v.price = data.price
    db.commit()
    db.refresh(v)
    return v


@router.get("/products/{product_id}/history", response_model=List[PriceHistoryOut])
def price_history(product_id: int, db: Session = Depends(get_db), user: User = Depends(price_managers)):
    get_or_404(db, Product, product_id)
    return (db.query(PriceHistory).filter(PriceHistory.product_id == product_id)
            .order_by(PriceHistory.id.desc()).all())


@router.get("/on-sale", response_model=List[CurrentPriceOut], summary="Products with a sale running now")
def on_sale(db: Session = Depends(get_db)):
    now = utcnow()
    rows = (db.query(Product).filter(Product.status == "active", Product.sale_price.isnot(None))
            .filter((Product.sale_start.is_(None)) | (Product.sale_start <= now))
            .filter((Product.sale_end.is_(None)) | (Product.sale_end >= now)).all())
    return [_current(p) for p in rows]