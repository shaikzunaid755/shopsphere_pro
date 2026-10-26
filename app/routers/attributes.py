# Module 11: Product Attributes
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, PRODUCT_MANAGER
from app.models import Attribute, ProductAttributeValue, Product, Category, User
from app.schemas.catalog import (AttributeCreate, AttributeUpdate, AttributeOut, ProductAttributeSet,
                                 AttrValueOut, ProductOut)
from app.schemas.common import Message
from app.services.pricing_service import category_chain

router = APIRouter(tags=["11 Product Attributes"])

managers = require_roles(ADMIN, PRODUCT_MANAGER)


def _validate_value(attr: Attribute, value: str):
    if attr.data_type == "number":
        try:
            float(value)
        except ValueError:
            raise HTTPException(400, f"{attr.name} must be a number")
    if attr.data_type == "choice":
        allowed = [a.strip().lower() for a in (attr.allowed_values or "").split(",")]
        if value.strip().lower() not in allowed:
            raise HTTPException(400, f"{attr.name} must be one of: {attr.allowed_values}")


@router.post("/attributes", response_model=AttributeOut, status_code=201)
def create_attribute(data: AttributeCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    if db.query(Attribute).filter(Attribute.name == data.name).first():
        raise HTTPException(409, "Attribute already exists")
    if data.category_id and not db.get(Category, data.category_id):
        raise HTTPException(400, "Category not found")
    attr = Attribute(name=data.name, data_type=data.data_type, category_id=data.category_id,
                     allowed_values=",".join(v.strip() for v in data.allowed_values) if data.allowed_values else None)
    db.add(attr)
    db.flush()
    audit(db, user, "attribute_created", "attribute", attr.id, None, data.model_dump())
    db.commit()
    db.refresh(attr)
    return attr


@router.get("/attributes", response_model=List[AttributeOut])
def list_attributes(category_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(Attribute).filter(Attribute.is_active == True)  # noqa
    if category_id:
        # global ones + the ones for this category or its parents
        chain = category_chain(db, category_id)
        q = q.filter((Attribute.category_id.is_(None)) | (Attribute.category_id.in_(chain)))
    return q.all()


@router.put("/attributes/{attribute_id}", response_model=AttributeOut)
def update_attribute(attribute_id: int, data: AttributeUpdate, db: Session = Depends(get_db),
                     user: User = Depends(managers)):
    attr = get_or_404(db, Attribute, attribute_id)
    changes = data.model_dump(exclude_unset=True)
    if "allowed_values" in changes and changes["allowed_values"] is not None:
        changes["allowed_values"] = ",".join(v.strip() for v in changes["allowed_values"])
    for k, v in changes.items():
        setattr(attr, k, v)
    audit(db, user, "attribute_updated", "attribute", attr.id, None, changes)
    db.commit()
    db.refresh(attr)
    return attr


@router.put("/products/{product_id}/attributes", response_model=List[AttrValueOut],
            summary="Assign / update attribute values on a product")
def set_product_attributes(product_id: int, values: List[ProductAttributeSet], db: Session = Depends(get_db),
                           user: User = Depends(managers)):
    product = get_or_404(db, Product, product_id)
    chain = category_chain(db, product.category_id)
    for item in values:
        attr = db.get(Attribute, item.attribute_id)
        if not attr or not attr.is_active:
            raise HTTPException(400, f"Attribute {item.attribute_id} not found")
        if attr.category_id and attr.category_id not in chain:
            raise HTTPException(400, f"Attribute '{attr.name}' is not allowed for this product's category")
        _validate_value(attr, item.value)
        row = db.query(ProductAttributeValue).filter(ProductAttributeValue.product_id == product.id,
                                                     ProductAttributeValue.attribute_id == attr.id).first()
        if row:
            row.value = item.value.strip()
        else:
            db.add(ProductAttributeValue(product_id=product.id, attribute_id=attr.id, value=item.value.strip()))
    audit(db, user, "product_attributes_set", "product", product.id, None, [v.model_dump() for v in values])
    db.commit()
    db.refresh(product)
    return [AttrValueOut(attribute_id=av.attribute_id, name=av.attribute.name, value=av.value)
            for av in product.attribute_values]


@router.get("/products/{product_id}/attributes", response_model=List[AttrValueOut])
def get_product_attributes(product_id: int, db: Session = Depends(get_db)):
    product = get_or_404(db, Product, product_id)
    return [AttrValueOut(attribute_id=av.attribute_id, name=av.attribute.name, value=av.value)
            for av in product.attribute_values]


@router.delete("/products/{product_id}/attributes/{attribute_id}", response_model=Message)
def remove_product_attribute(product_id: int, attribute_id: int, db: Session = Depends(get_db),
                             user: User = Depends(managers)):
    row = db.query(ProductAttributeValue).filter(ProductAttributeValue.product_id == product_id,
                                                 ProductAttributeValue.attribute_id == attribute_id).first()
    if not row:
        raise HTTPException(404, "Attribute not set on this product")
    db.delete(row)
    db.commit()
    return {"message": "Removed"}


@router.get("/attributes/filter/products", response_model=List[ProductOut],
            summary="Filter products by attribute, e.g. ?attribute_id=1&value=Cotton")
def filter_by_attribute(attribute_id: int, value: str, category_id: Optional[int] = None,
                        page: int = Query(1, ge=1), size: int = Query(20, le=100), db: Session = Depends(get_db)):
    q = (db.query(Product).join(ProductAttributeValue, ProductAttributeValue.product_id == Product.id)
         .filter(ProductAttributeValue.attribute_id == attribute_id,
                 ProductAttributeValue.value.ilike(value.strip()),
                 Product.status == "active"))
    if category_id:
        q = q.filter(Product.category_id == category_id)
    return q.offset((page - 1) * size).limit(size).all()