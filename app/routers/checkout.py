# Module 24: Checkout Management
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.roles import CUSTOMER
from app.models import Cart, Address, User
from app.schemas.cart import CheckoutSummary
from app.schemas.order import CheckoutRequest, CheckoutValidateRequest, OrderDetail
from app.services import order_service, pricing_service as pricing
from app.routers.orders import order_detail

router = APIRouter(prefix="/checkout", tags=["24 Checkout Management"])

customer_only = require_roles(CUSTOMER)


def _clean(data):
    # swagger fills empty fields with 0 / "string", treat them as not sent
    data.billing_address_id = data.billing_address_id or None
    if data.coupon_code in ("", "string"):
        data.coupon_code = None
    if getattr(data, "idempotency_key", None) == "string":
        data.idempotency_key = None
    return data

@router.post("/validate", response_model=CheckoutSummary,
             summary="Validate cart, addresses, stock and recalculate everything (no order created)")
def validate_checkout(data: CheckoutValidateRequest, user: User = Depends(customer_only),
                      db: Session = Depends(get_db)):
    data = _clean(data)
    cart = db.query(Cart).filter(Cart.user_id == user.id).first()
    if not cart or not cart.items:
        raise HTTPException(400, "Your cart is empty")
    ship = order_service._user_address(db, user, data.shipping_address_id, "shipping")
    order_service._user_address(db, user, data.billing_address_id or data.shipping_address_id, "billing")
    items = [(ci.product, ci.variant, ci.quantity) for ci in cart.items]
    is_cod = data.payment_method == "cod"
    calc = pricing.calculate(db, user.id, items, data.coupon_code or cart.coupon_code,
                             data.shipping_method_id, ship.state, cod=is_cod)
    if is_cod and not calc["errors"]:
        order_service._cod_check(db, calc, ship, [p for p, _, _ in items])
    calc.pop("_coupon", None)
    return calc

@router.post("/place-order", response_model=OrderDetail, status_code=201,
             summary="Create the order (pending) and reserve stock. Safe to retry with same Idempotency-Key")
def place_order(data: CheckoutRequest, user: User = Depends(customer_only), db: Session = Depends(get_db),
                idempotency_key: str = Header(None, alias="Idempotency-Key")):
    if not user.is_email_verified:
        raise HTTPException(403, "Verify your email before placing orders")
    data = _clean(data)
    if idempotency_key and not data.idempotency_key:
        data.idempotency_key = idempotency_key
    try:
        order, created = order_service.place_order(db, user, data)
    except HTTPException:
        db.rollback()  # stock reservations and coupon usage are undone together
        raise
    return order_detail(order)
