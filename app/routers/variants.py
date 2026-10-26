# Module 9: Product Variants
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, PRODUCT_MANAGER
from app.models import Product, ProductVariant, User, PriceHistory
from app.schemas.catalog import (VariantCreate, VariantUpdate, VariantOut, VariantStatus, VariantAvailability)
from app.services.inventory_service import total_available

router = APIRouter(tags=["09 Product Variants"])

managers = require_roles(ADMIN, PRODUCT_MANAGER)


def _variant_name(data):
    parts = [data.get("color"), data.get("size"), data.get("other_option")]
    return " / ".join(p for p in parts if p) or None



@router.post("/products/{product_id}/variants", response_model=VariantOut, status_code=201)
def add_variant(product_id: int, data: VariantCreate, db: Session = Depends(get_db),
                user: User = Depends(managers)):
    product = get_or_404(db, Product, product_id)
    sku = data.sku.upper()
    if db.query(ProductVariant).filter(ProductVariant.sku == sku).first() or \
            db.query(Product).filter(Product.sku == sku).first():
        raise HTTPException(409, "SKU already used")
    # same size+color twice on one product is a mistake
    dup = db.query(ProductVariant).filter(ProductVariant.product_id == product.id,
                                          ProductVariant.size == data.size,
                                          ProductVariant.color == data.color,
                                          ProductVariant.other_option == data.other_option).first()
    if dup:
        raise HTTPException(409, f"Variant with same options exists ({dup.sku})")
    values = data.model_dump()
    v = ProductVariant(product_id=product.id, **values)
    v.sku = sku
    v.name = data.name or _variant_name(values)
    db.add(v)
    db.flush()
    if data.price is not None:
        db.add(PriceHistory(product_id=product.id, variant_id=v.id, new_base_price=data.price,
                            reason="variant created", changed_by=user.id))
    audit(db, user, "variant_created", "variant", v.id, None, {"sku": v.sku, "product_id": product.id})
    db.commit()
    db.refresh(v)
    return v


@router.get("/products/{product_id}/variants", response_model=List[VariantOut])
def list_variants(product_id: int, active_only: bool = False, db: Session = Depends(get_db)):
    get_or_404(db, Product, product_id)
    q = db.query(ProductVariant).filter(ProductVariant.product_id == product_id)
    if active_only:
        q = q.filter(ProductVariant.status == "active")
    return q.all()


@router.get("/products/{product_id}/variants/availability", response_model=List[VariantAvailability])
def variants_availability(product_id: int, db: Session = Depends(get_db)):
    get_or_404(db, Product, product_id)
    out = []
    for v in db.query(ProductVariant).filter(ProductVariant.product_id == product_id).all():
        avail = total_available(db, product_id, v.id)
        out.append(VariantAvailability(variant_id=v.id, sku=v.sku, status=v.status, available=avail,
                                       in_stock=avail > 0 and v.status == "active"))
    return out


@router.get("/variants/{variant_id}", response_model=VariantOut)
def get_variant(variant_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, ProductVariant, variant_id, "Variant")


@router.put("/variants/{variant_id}", response_model=VariantOut)
def update_variant(variant_id: int, data: VariantUpdate, db: Session = Depends(get_db),
                   user: User = Depends(managers)):
    v = get_or_404(db, ProductVariant, variant_id, "Variant")
    changes = data.model_dump(exclude_unset=True)
    old = {k: getattr(v, k) for k in changes}
    if "price" in changes and changes["price"] != v.price:
        db.add(PriceHistory(product_id=v.product_id, variant_id=v.id, old_base_price=v.price,
                            new_base_price=changes["price"], reason="variant price update", changed_by=user.id))
    for k, val in changes.items():
        setattr(v, k, val)
    if not data.name and any(k in changes for k in ("size", "color", "other_option")):
        v.name = _variant_name({"size": v.size, "color": v.color, "other_option": v.other_option})
    audit(db, user, "variant_updated", "variant", v.id, old, changes)
    db.commit()
    db.refresh(v)
    return v


@router.patch("/variants/{variant_id}/status", response_model=VariantOut)
def variant_status(variant_id: int, data: VariantStatus, db: Session = Depends(get_db),
                   user: User = Depends(managers)):
    v = get_or_404(db, ProductVariant, variant_id, "Variant")
    old = v.status
    v.status = data.status
    audit(db, user, "variant_status", "variant", v.id, {"status": old}, {"status": data.status})
    db.commit()
    db.refresh(v)
    return v


@router.get("/variants/{variant_id}/availability", response_model=VariantAvailability)
def variant_availability(variant_id: int, db: Session = Depends(get_db)):
    v = get_or_404(db, ProductVariant, variant_id, "Variant")
    avail = total_available(db, v.product_id, v.id)
    return VariantAvailability(variant_id=v.id, sku=v.sku, status=v.status, available=avail,
                               in_stock=avail > 0 and v.status == "active")