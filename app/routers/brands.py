# Module 7: Brand Management
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, PRODUCT_MANAGER
from app.models import Brand, Product, User
from app.schemas.catalog import BrandCreate, BrandUpdate, BrandOut, BrandStatus, ProductOut

router = APIRouter(prefix="/brands", tags=["07 Brand Management"])

managers = require_roles(ADMIN, PRODUCT_MANAGER)


@router.post("", response_model=BrandOut, status_code=201)
def create_brand(data: BrandCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    if db.query(Brand).filter(Brand.name == data.name.strip()).first():
        raise HTTPException(409, "Brand already exists")
    brand = Brand(**data.model_dump())
    brand.name = brand.name.strip()
    db.add(brand)
    db.flush()
    audit(db, user, "brand_created", "brand", brand.id, None, data.model_dump())
    db.commit()
    db.refresh(brand)
    return brand


@router.get("", response_model=List[BrandOut], summary="List / search brands")
def list_brands(search: Optional[str] = None, active_only: bool = True, db: Session = Depends(get_db)):
    q = db.query(Brand)
    if search:
        q = q.filter(Brand.name.ilike(f"%{search}%"))
    if active_only:
        q = q.filter(Brand.is_active == True)  # noqa
    return q.order_by(Brand.name).all()


@router.get("/{brand_id}", response_model=BrandOut)
def get_brand(brand_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, Brand, brand_id)


@router.put("/{brand_id}", response_model=BrandOut)
def update_brand(brand_id: int, data: BrandUpdate, db: Session = Depends(get_db), user: User = Depends(managers)):
    brand = get_or_404(db, Brand, brand_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        clash = db.query(Brand).filter(Brand.name == changes["name"], Brand.id != brand.id).first()
        if clash:
            raise HTTPException(409, "Another brand has this name")
    old = {k: getattr(brand, k) for k in changes}
    for k, v in changes.items():
        setattr(brand, k, v)
    audit(db, user, "brand_updated", "brand", brand.id, old, changes)
    db.commit()
    db.refresh(brand)
    return brand


@router.patch("/{brand_id}/status", response_model=BrandOut)
def brand_status(brand_id: int, data: BrandStatus, db: Session = Depends(get_db), user: User = Depends(managers)):
    brand = get_or_404(db, Brand, brand_id)
    brand.is_active = data.is_active
    audit(db, user, "brand_status", "brand", brand.id, None, {"is_active": data.is_active})
    db.commit()
    db.refresh(brand)
    return brand


@router.get("/{brand_id}/products", response_model=List[ProductOut])
def brand_products(brand_id: int, page: int = Query(1, ge=1), size: int = Query(20, le=100),
                   db: Session = Depends(get_db)):
    get_or_404(db, Brand, brand_id)
    return (db.query(Product).filter(Product.brand_id == brand_id, Product.status == "active")
            .offset((page - 1) * size).limit(size).all())


@router.put("/{brand_id}/products/{product_id}", response_model=ProductOut, summary="Link product to brand")
def link_product(brand_id: int, product_id: int, db: Session = Depends(get_db), user: User = Depends(managers)):
    brand = get_or_404(db, Brand, brand_id)
    if not brand.is_active:
        raise HTTPException(400, "Brand is inactive")
    product = get_or_404(db, Product, product_id)
    old = product.brand_id
    product.brand_id = brand.id
    audit(db, user, "product_brand_changed", "product", product.id, {"brand_id": old}, {"brand_id": brand.id})
    db.commit()
    db.refresh(product)
    return product