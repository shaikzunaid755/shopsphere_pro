# Module 8: Product Management
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, PRODUCT_MANAGER
from app.core.utils import slugify
from app.models import (Product, Category, Brand, User, Supplier, ProductAttributeValue, PriceHistory)
from app.schemas.catalog import (ProductCreate, ProductUpdate, ProductOut, ProductDetail, ProductList,
                                 ProductStatus, VariantOut, AttrValueOut, FileOut)
from app.services.inventory_service import total_available
from app.services.pricing_service import category_chain

router = APIRouter(prefix="/products", tags=["08 Product Management"])

managers = require_roles(ADMIN, PRODUCT_MANAGER)


def _validate_links(db, category_id=None, brand_id=None, supplier_id=None):
    if category_id is not None:
        cat = db.get(Category, category_id)
        if not cat or not cat.is_active:
            raise HTTPException(400, "Category not found or inactive")
    if brand_id is not None:
        brand = db.get(Brand, brand_id)
        if not brand or not brand.is_active:
            raise HTTPException(400, "Brand not found or inactive")
    if supplier_id is not None:
        sup = db.get(Supplier, supplier_id)
        if not sup or sup.status != "approved":
            raise HTTPException(400, "Supplier not found or not approved")


def _unique_slug(db, name, skip_id=None):
    base = slugify(name)
    slug, n = base, 1
    while True:
        q = db.query(Product).filter(Product.slug == slug)
        if skip_id:
            q = q.filter(Product.id != skip_id)
        if not q.first():
            return slug
        n += 1
        slug = f"{base}-{n}"


def product_detail(db, p: Product) -> ProductDetail:
    base = ProductOut.model_validate(p).model_dump()
    if p.variants:
        stock = sum(total_available(db, p.id, v.id) for v in p.variants)
    else:
        stock = total_available(db, p.id)
    return ProductDetail(
        **base,
        current_price=p.current_price(),
        category_name=p.category.name if p.category else None,
        brand_name=p.brand.name if p.brand else None,
        in_stock=stock,
        variants=[VariantOut.model_validate(v) for v in p.variants],
        attributes=[AttrValueOut(attribute_id=av.attribute_id, name=av.attribute.name, value=av.value)
                    for av in p.attribute_values],
        files=[FileOut.model_validate(f) for f in p.files],
    )


@router.post("", response_model=ProductOut, status_code=201)
def create_product(data: ProductCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    if db.query(Product).filter(Product.sku == data.sku.upper()).first():
        raise HTTPException(409, "SKU already exists")
    _validate_links(db, data.category_id, data.brand_id, data.supplier_id)
    product = Product(**data.model_dump(), slug=_unique_slug(db, data.name), created_by=user.id)
    product.sku = data.sku.upper()
    db.add(product)
    db.flush()
    db.add(PriceHistory(product_id=product.id, new_base_price=product.base_price,
                        new_sale_price=product.sale_price, valid_from=product.sale_start,
                        valid_to=product.sale_end, reason="initial price", changed_by=user.id))
    audit(db, user, "product_created", "product", product.id, None, {"sku": product.sku, "name": product.name})
    db.commit()
    db.refresh(product)
    return product


@router.get("", response_model=ProductList, summary="Search and filter products")
def list_products(
    search: Optional[str] = None,
    category_id: Optional[int] = None,
    brand_id: Optional[int] = None,
    min_price: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
    status: str = "active",
    sort: str = Query("newest", pattern="^(newest|price_asc|price_desc|rating|name)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(Product)
    if status != "all":
        q = q.filter(Product.status == status)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Product.name.ilike(like), Product.description.ilike(like), Product.sku.ilike(like)))
    if category_id:
        # include subcategories too
        ids = [c.id for c in db.query(Category).all() if category_id in category_chain(db, c.id)]
        q = q.filter(Product.category_id.in_(ids or [category_id]))
    if brand_id:
        q = q.filter(Product.brand_id == brand_id)
    if min_price is not None:
        q = q.filter(Product.base_price >= min_price)
    if max_price is not None:
        q = q.filter(Product.base_price <= max_price)

    order = {
        "newest": Product.id.desc(),
        "price_asc": Product.base_price.asc(),
        "price_desc": Product.base_price.desc(),
        "rating": Product.avg_rating.desc(),
        "name": Product.name.asc(),
    }[sort]
    total = q.count()
    items = q.order_by(order).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/slug/{slug}", response_model=ProductDetail)
def by_slug(slug: str, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.slug == slug).first()
    if not p:
        raise HTTPException(404, "Product not found")
    return product_detail(db, p)


@router.get("/{product_id}", response_model=ProductDetail)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return product_detail(db, get_or_404(db, Product, product_id))


@router.put("/{product_id}", response_model=ProductOut)
def update_product(product_id: int, data: ProductUpdate, db: Session = Depends(get_db),
                   user: User = Depends(managers)):
    p = get_or_404(db, Product, product_id)
    changes = data.model_dump(exclude_unset=True)
    _validate_links(db, changes.get("category_id"), changes.get("brand_id"), changes.get("supplier_id"))
    if "category_id" in changes and changes["category_id"] != p.category_id:
        # attribute values bound to the old category don't make sense anymore
        for av in list(p.attribute_values):
            if av.attribute.category_id and av.attribute.category_id not in category_chain(db, changes["category_id"]):
                db.delete(av)
    old = {k: getattr(p, k) for k in changes}
    for k, v in changes.items():
        setattr(p, k, v)
    if "name" in changes:
        p.slug = _unique_slug(db, p.name, skip_id=p.id)
    audit(db, user, "product_updated", "product", p.id, old, changes)
    db.commit()
    db.refresh(p)
    return p


@router.patch("/{product_id}/status", response_model=ProductOut, summary="Activate / deactivate product")
def product_status(product_id: int, data: ProductStatus, db: Session = Depends(get_db),
                   user: User = Depends(managers)):
    p = get_or_404(db, Product, product_id)
    old = p.status
    p.status = data.status
    audit(db, user, "product_status", "product", p.id, {"status": old}, {"status": data.status})
    db.commit()
    db.refresh(p)
    return p


@router.delete("/{product_id}", response_model=ProductOut, summary="Deactivate product (soft delete)")
def deactivate_product(product_id: int, db: Session = Depends(get_db), user: User = Depends(managers)):
    return product_status(product_id, ProductStatus(status="inactive"), db, user)