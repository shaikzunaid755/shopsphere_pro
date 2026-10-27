# Module 17: Shopping Cart Management  +  Module 18: Cart Price Calculation
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.roles import CUSTOMER
from app.core.utils import money
from app.models import Cart, CartItem, Product, ProductVariant, Address, User
from app.schemas.cart import (CartItemAdd, CartItemUpdate, CartOut, CartLine, ApplyCoupon,
                              CartSummaryRequest, CheckoutSummary)
from app.services import pricing_service as pricing
from app.services.inventory_service import total_available

router = APIRouter(prefix="/cart", tags=["17 Shopping Cart"])
calc_router = APIRouter(prefix="/cart", tags=["18 Cart Price Calculation"])

customer_only = require_roles(CUSTOMER)

def get_cart(db, user):
    # every customer has one cart, created the first time they need it
    cart = db.query(Cart).filter(Cart.user_id == user.id).first()
    if not cart:
        cart = Cart(user_id=user.id)
        db.add(cart)
        db.commit()
        db.refresh(cart)
    return cart


def cart_view(db, cart) -> CartOut:
    lines, warnings = [], []
    subtotal = money(0)
    for ci in cart.items:
        p, v = ci.product, ci.variant
        price = pricing.unit_price(p, v)
        avail = total_available(db, p.id, v.id if v else None)
        ok = p.status == "active" and (not v or v.status == "active") and avail >= ci.quantity
        if not ok:
            if avail < ci.quantity:
                warnings.append(f"{p.name}: only {avail} available")
            else:
                warnings.append(f"{p.name} is unavailable")
        line_amount = money(price * ci.quantity)
        subtotal += line_amount
        lines.append(CartLine(
            item_id=ci.id, product_id=p.id, variant_id=v.id if v else None,
            name=p.name, variant_name=v.name if v else None,
            sku=v.sku if v else p.sku, quantity=ci.quantity,
            unit_price=price, line_amount=line_amount,
            available_stock=avail, in_stock=ok))
    return CartOut(cart_id=cart.id, items=lines,
                   item_count=sum(l.quantity for l in lines),
                   subtotal=money(subtotal), coupon_code=cart.coupon_code,
                   warnings=warnings)

def _check_product(db, product_id, variant_id, qty):
    product = db.get(Product, product_id)
    if not product or product.status != "active":
        raise HTTPException(404, "Product not available")
    variant = None
    if variant_id:
        variant = db.get(ProductVariant, variant_id)
        if not variant or variant.product_id != product.id or variant.status != "active":
            raise HTTPException(400, "Variant not available")
    elif product.variants:
        raise HTTPException(400, "Please choose a variant (size/color) for this product")
    avail = total_available(db, product.id, variant_id)
    if avail < qty:
        raise HTTPException(409, f"Only {avail} in stock")
    return product, variant

@router.get("", response_model=CartOut)
def view_cart(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    return cart_view(db, get_cart(db, user))


@router.post("/items", response_model=CartOut, status_code=201)
def add_item(data: CartItemAdd, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    cart = get_cart(db, user)
    existing = next((ci for ci in cart.items
                     if ci.product_id == data.product_id and ci.variant_id == data.variant_id), None)
    new_qty = data.quantity + (existing.quantity if existing else 0)
    if new_qty > 20:
        raise HTTPException(400, "Max 20 units per item")
    _check_product(db, data.product_id, data.variant_id, new_qty)
    if existing:
        existing.quantity = new_qty
    else:
        cart.items.append(CartItem(product_id=data.product_id, variant_id=data.variant_id,
                                   quantity=data.quantity))
    db.commit()
    db.refresh(cart)
    return cart_view(db, cart)

def _my_item(cart, item_id):
    # only items from MY cart, so nobody can touch another customer's cart
    item = next((ci for ci in cart.items if ci.id == item_id), None)
    if not item:
        raise HTTPException(404, "Item not in your cart")
    return item


@router.put("/items/{item_id}", response_model=CartOut)
def update_item(item_id: int, data: CartItemUpdate, user: User = Depends(customer_only),
                db: Session = Depends(get_db)):
    cart = get_cart(db, user)
    item = _my_item(cart, item_id)
    _check_product(db, item.product_id, item.variant_id, data.quantity)
    item.quantity = data.quantity
    db.commit()
    db.refresh(cart)
    return cart_view(db, cart)

@router.delete("/items/{item_id}", response_model=CartOut)
def remove_item(item_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    cart = get_cart(db, user)
    db.delete(_my_item(cart, item_id))
    db.commit()
    db.refresh(cart)
    return cart_view(db, cart)


@router.delete("", response_model=CartOut, summary="Empty the cart")
def clear_cart(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    cart = get_cart(db, user)
    for ci in list(cart.items):
        db.delete(ci)
    cart.coupon_code = None
    db.commit()
    db.refresh(cart)
    return cart_view(db, cart)

@router.get("/validate", summary="Check every item is still available")
def validate_cart(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    view = cart_view(db, get_cart(db, user))
    ok = not view.warnings and view.item_count > 0
    return {"valid": ok, "warnings": view.warnings, "item_count": view.item_count}


# ---------- Module 18 ----------
@calc_router.post("/coupon", response_model=CartOut, summary="Apply coupon to cart")
def apply_coupon(data: ApplyCoupon, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    cart = get_cart(db, user)
    if not cart.items:
        raise HTTPException(400, "Cart is empty")
    items = [(ci.product, ci.variant, ci.quantity) for ci in cart.items]
    calc = pricing.calculate(db, user.id, items, check_stock=False)
    after_promo = calc["subtotal"] - calc["promotion_discount"]
    # raises 400 if expired, min order not met, usage limit reached...
    coupon, _ = pricing.validate_coupon(db, data.code, user.id, after_promo)
    cart.coupon_code = coupon.code
    db.commit()
    db.refresh(cart)
    return cart_view(db, cart)

@calc_router.delete("/coupon", response_model=CartOut)
def remove_coupon(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    cart = get_cart(db, user)
    cart.coupon_code = None
    db.commit()
    db.refresh(cart)
    return cart_view(db, cart)


@calc_router.post("/summary", response_model=CheckoutSummary,
                  summary="Subtotal, discounts, tax, shipping and final total (server side prices)")
def cart_summary(data: CartSummaryRequest, user: User = Depends(customer_only),
                 db: Session = Depends(get_db)):
    cart = get_cart(db, user)
    if not cart.items:
        raise HTTPException(400, "Cart is empty")
    state = None
    if data.address_id:
        addr = db.get(Address, data.address_id)
        if not addr or addr.user_id != user.id or addr.is_deleted:
            raise HTTPException(400, "Invalid address")
        state = addr.state
    items = [(ci.product, ci.variant, ci.quantity) for ci in cart.items]
    calc = pricing.calculate(db, user.id, items, data.coupon_code or cart.coupon_code,
                             data.shipping_method_id, state, cod=data.payment_method == "cod")
    calc.pop("_coupon", None)
    return calc