# Module 19: Wishlist Management
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.roles import CUSTOMER
from app.models import WishlistItem, Product, ProductVariant, User
from app.schemas.cart import WishlistAdd, WishlistOut, CartItemAdd
from app.schemas.common import Message
from app.services.inventory_service import total_available
from app.services.pricing_service import unit_price

router = APIRouter(prefix="/wishlist", tags=["19 Wishlist"])

customer_only = require_roles(CUSTOMER)

def _out(db, w):
    p = w.product
    v = db.get(ProductVariant, w.variant_id) if w.variant_id else None
    avail = total_available(db, p.id, w.variant_id)
    status = p.status
    if v and p.status == "active":
        status = v.status
    return WishlistOut(id=w.id, product_id=p.id, variant_id=w.variant_id, product_name=p.name,
                       current_price=unit_price(p, v), status=status, available=avail,
                       in_stock=avail > 0 and status == "active", created_at=w.created_at)


@router.get("", response_model=List[WishlistOut])
def my_wishlist(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    rows = (db.query(WishlistItem).filter(WishlistItem.user_id == user.id)
            .order_by(WishlistItem.id.desc()).all())
    return [_out(db, w) for w in rows]

@router.post("", response_model=WishlistOut, status_code=201)
def add(data: WishlistAdd, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    product = db.get(Product, data.product_id)
    if not product or product.status != "active":
        raise HTTPException(404, "Product not available")
    if data.variant_id:
        v = db.get(ProductVariant, data.variant_id)
        if not v or v.product_id != product.id or v.status != "active":
            raise HTTPException(400, "Variant not available")
    mine = db.query(WishlistItem).filter(WishlistItem.user_id == user.id)
    for w in mine.filter(WishlistItem.product_id == product.id).all():
        if w.variant_id == data.variant_id:
            raise HTTPException(409, "Already in your wishlist")
    if mine.count() >= 100:
        raise HTTPException(400, "Wishlist is full (100 items)")
    w = WishlistItem(user_id=user.id, product_id=product.id, variant_id=data.variant_id)
    db.add(w)
    db.commit()
    db.refresh(w)
    return _out(db, w)

def _my_item(db, user, item_id):
    w = db.get(WishlistItem, item_id)
    if not w or w.user_id != user.id:
        raise HTTPException(404, "Not in your wishlist")
    return w


@router.delete("/{item_id}", response_model=Message)
def remove(item_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    db.delete(_my_item(db, user, item_id))
    db.commit()
    return {"message": "Removed from wishlist"}


@router.post("/{item_id}/move-to-cart", response_model=Message)
def move_to_cart(item_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    from app.routers.cart import add_item   # reuse the cart logic (stock check etc)
    w = _my_item(db, user, item_id)
    add_item(CartItemAdd(product_id=w.product_id, variant_id=w.variant_id, quantity=1), user, db)
    db.delete(w)
    db.commit()
    return {"message": "Moved to cart"}