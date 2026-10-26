# Module 6: Product Category Management
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, PRODUCT_MANAGER
from app.core.utils import slugify
from app.models import Category, Product, User, Attribute, Promotion, TaxRule
from app.schemas.catalog import CategoryCreate, CategoryUpdate, CategoryOut, CategoryTree, CategoryStatus
from app.schemas.common import Message

router = APIRouter(prefix="/categories", tags=["06 Category Management"])

managers = require_roles(ADMIN, PRODUCT_MANAGER)


def _unique_slug(db, name, skip_id=None):
    base = slugify(name)
    slug, n = base, 1
    while True:
        q = db.query(Category).filter(Category.slug == slug)
        if skip_id:
            q = q.filter(Category.id != skip_id)
        if not q.first():
            return slug
        n += 1
        slug = f"{base}-{n}"


def _check_parent(db, parent_id, own_id=None):
    if parent_id is None:
        return
    parent = db.get(Category, parent_id)
    if not parent:
        raise HTTPException(400, "Parent category not found")
    # walk up to make sure we don't create a loop
    node = parent
    while node:
        if own_id and node.id == own_id:
            raise HTTPException(400, "Category cannot be its own parent / child")
        node = db.get(Category, node.parent_id) if node.parent_id else None


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(data: CategoryCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    _check_parent(db, data.parent_id)
    dup = db.query(Category).filter(Category.name == data.name, Category.parent_id == data.parent_id).first()
    if dup:
        raise HTTPException(409, "Category with this name already exists at this level")
    cat = Category(name=data.name, description=data.description, parent_id=data.parent_id,
                   slug=_unique_slug(db, data.name))
    db.add(cat)
    db.flush()
    audit(db, user, "category_created", "category", cat.id, None, data.model_dump())
    db.commit()
    db.refresh(cat)
    return cat


@router.get("", response_model=List[CategoryOut])
def list_categories(parent_id: Optional[int] = None, active_only: bool = True, db: Session = Depends(get_db)):
    q = db.query(Category)
    if parent_id is not None:
        q = q.filter(Category.parent_id == parent_id)
    if active_only:
        q = q.filter(Category.is_active == True)  # noqa
    return q.order_by(Category.name).all()


def _build_tree(cat, active_only):
    node = CategoryTree.model_validate(cat)
    node.children = [_build_tree(c, active_only) for c in cat.children if c.is_active or not active_only]
    return node


@router.get("/tree", response_model=List[CategoryTree])
def category_tree(active_only: bool = True, db: Session = Depends(get_db)):
    roots = db.query(Category).filter(Category.parent_id.is_(None))
    if active_only:
        roots = roots.filter(Category.is_active == True)  # noqa
    return [_build_tree(c, active_only) for c in roots.order_by(Category.name).all()]


@router.get("/{category_id}", response_model=CategoryTree)
def get_category(category_id: int, db: Session = Depends(get_db)):
    return _build_tree(get_or_404(db, Category, category_id), True)


@router.get("/{category_id}/path", response_model=List[CategoryOut], summary="Breadcrumb from root")
def category_path(category_id: int, db: Session = Depends(get_db)):
    cat = get_or_404(db, Category, category_id)
    path = []
    while cat:
        path.insert(0, cat)
        cat = db.get(Category, cat.parent_id) if cat.parent_id else None
    return path


@router.put("/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, data: CategoryUpdate, db: Session = Depends(get_db),
                    user: User = Depends(managers)):
    cat = get_or_404(db, Category, category_id)
    changes = data.model_dump(exclude_unset=True)
    if "parent_id" in changes:
        _check_parent(db, changes["parent_id"], own_id=cat.id)
    old = {k: getattr(cat, k) for k in changes}
    for k, v in changes.items():
        setattr(cat, k, v)
    if "name" in changes:
        cat.slug = _unique_slug(db, cat.name, skip_id=cat.id)
    audit(db, user, "category_updated", "category", cat.id, old, changes)
    db.commit()
    db.refresh(cat)
    return cat


@router.patch("/{category_id}/status", response_model=CategoryOut, summary="Activate or deactivate")
def set_status(category_id: int, data: CategoryStatus, db: Session = Depends(get_db),
               user: User = Depends(managers)):
    cat = get_or_404(db, Category, category_id)
    if not data.is_active:
        active_kids = [c for c in cat.children if c.is_active]
        if active_kids:
            raise HTTPException(400, "Deactivate subcategories first")
    if data.is_active and cat.parent_id:
        parent = db.get(Category, cat.parent_id)
        if parent and not parent.is_active:
            raise HTTPException(400, "Parent category is inactive")
    cat.is_active = data.is_active
    audit(db, user, "category_status", "category", cat.id, None, {"is_active": data.is_active})
    db.commit()
    db.refresh(cat)
    return cat


@router.delete("/{category_id}", response_model=Message)
def delete_category(category_id: int, db: Session = Depends(get_db), user: User = Depends(managers)):
    cat = get_or_404(db, Category, category_id)
    if cat.children:
        raise HTTPException(400, "Category has subcategories, delete or move them first")
    if db.query(Product).filter(Product.category_id == cat.id).count():
        raise HTTPException(400, "Category has products, move them or deactivate the category instead")
    for model in (Attribute, Promotion, TaxRule):
        if db.query(model).filter(model.category_id == cat.id).count():
            raise HTTPException(400, f"Category is used by {model.__tablename__}")
    audit(db, user, "category_deleted", "category", cat.id, {"name": cat.name}, None)
    db.delete(cat)
    db.commit()
    return {"message": "Category deleted"}