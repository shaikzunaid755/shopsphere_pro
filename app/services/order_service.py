from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import update

from app.core.audit import audit
from app.core.roles import ORDER_MANAGER, ADMIN
from app.core.utils import money, make_number, utcnow, to_json
from app.models import (Order, OrderItem, OrderStatusHistory, OrderCancellation, Cart, Address,
                        Payment, PaymentTransaction, Refund, Coupon, CouponUsage, CODRule, CODCollection,
                        User, Product)
from app.services import inventory_service as inv
from app.services import pricing_service as pricing
from app.services import payment_gateway as gateway
from app.services.notification_service import notify_customer, notify_roles

# which status can move to which
ALLOWED_TRANSITIONS = {
    "pending": ["confirmed", "cancelled"],
    "confirmed": ["processing", "cancelled"],
    "processing": ["shipped", "cancelled"],
    "shipped": ["delivered"],
    "delivered": [],
    "cancelled": [],
}

def add_history(db, order, old, new, user=None, note=None):
    db.add(OrderStatusHistory(order_id=order.id, from_status=old, to_status=new, note=note,
                              changed_by=user.id if user else None))


def change_status(db, order: Order, new_status: str, user=None, note=None):
    old = order.status
    if new_status == old:
        raise HTTPException(400, f"Order is already {old}")
    if new_status not in ALLOWED_TRANSITIONS.get(old, []):
        raise HTTPException(400, f"Cannot move order from '{old}' to '{new_status}'")
    if new_status == "confirmed" and order.payment_method == "online" and order.payment_status != "paid":
        raise HTTPException(400, "Online order can be confirmed only after payment succeeds")

    if new_status == "shipped":
        # reserved stock now really leaves the warehouse
        for item in order.items:
            if item.fulfillment_status == "cancelled":
                continue
            inv.commit_sale(db, item.product_id, item.variant_id, item.warehouse_id, item.quantity,
                            "order", order.id)
            item.fulfillment_status = "shipped"
    elif new_status == "delivered":
        for item in order.items:
            if item.fulfillment_status == "shipped":
                item.fulfillment_status = "delivered"
    elif new_status == "cancelled":
        # give the reserved stock back
        for item in order.items:
            if item.fulfillment_status in ("pending", "packed"):
                inv.release(db, item.product_id, item.variant_id, item.warehouse_id, item.quantity,
                            "order", order.id)
                item.fulfillment_status = "cancelled"
    elif new_status == "processing":
        for item in order.items:
            if item.fulfillment_status == "pending":
                item.fulfillment_status = "packed"

    order.status = new_status
    add_history(db, order, old, new_status, user, note)
    audit(db, user, "order_status_change", "order", order.id, {"status": old}, {"status": new_status})

    customer = db.get(User, order.user_id)
    if customer:
        notify_customer(db, customer, f"Order {order.order_number} {new_status}",
                        f"Your order {order.order_number} is now {new_status}.",
                        "order", "order", order.id, template=f"order_{new_status}",
                        sms=new_status in ("shipped", "delivered"))
    return order

def _cod_check(db, calc, address, products):
    rule = db.query(CODRule).first()
    if not rule or not rule.is_enabled:
        raise HTTPException(400, "Cash on delivery is currently disabled")
    total = calc["grand_total"]
    if total > money(rule.max_order_amount):
        raise HTTPException(400, f"COD is allowed only up to {rule.max_order_amount}")
    if total < money(rule.min_order_amount):
        raise HTTPException(400, f"COD needs minimum order of {rule.min_order_amount}")
    blocked = [p.strip() for p in (rule.blocked_postal_codes or "").split(",") if p.strip()]
    if address.postal_code in blocked:
        raise HTTPException(400, f"COD not available for pincode {address.postal_code}")
    for p in products:
        if not p.is_cod_allowed:
            raise HTTPException(400, f"{p.name} cannot be ordered with COD")


def _user_address(db, user, address_id, label):
    addr = db.get(Address, address_id)
    if not addr or addr.user_id != user.id or addr.is_deleted:
        raise HTTPException(400, f"Invalid {label} address")
    return addr

def place_order(db, user, data):
    # same key sent twice (double click / retry) returns the first order
    if data.idempotency_key:
        existing = db.query(Order).filter(Order.idempotency_key == data.idempotency_key).first()
        if existing:
            if existing.user_id != user.id:
                raise HTTPException(409, "Idempotency key already used")
            return existing, False

    cart = db.query(Cart).filter(Cart.user_id == user.id).first()
    if not cart or not cart.items:
        raise HTTPException(400, "Your cart is empty")

    ship_addr = _user_address(db, user, data.shipping_address_id, "shipping")
    bill_addr = _user_address(db, user, data.billing_address_id or data.shipping_address_id, "billing")

    items = [(ci.product, ci.variant, ci.quantity) for ci in cart.items]
    coupon_code = data.coupon_code or cart.coupon_code
    is_cod = data.payment_method == "cod"
    # prices always recalculated on the server, never taken from the request
    calc = pricing.calculate(db, user.id, items, coupon_code, data.shipping_method_id,
                             ship_addr.state, cod=is_cod)
    if calc["errors"]:
        raise HTTPException(409, {"message": "Cart has problems", "errors": calc["errors"]})
    if is_cod:
        _cod_check(db, calc, ship_addr, [p for p, _, _ in items])

    order = Order(
        order_number=make_number("ORD"), user_id=user.id, status="pending",
        payment_method=data.payment_method,
        payment_status="cod_pending" if is_cod else "pending",
        shipping_address_id=ship_addr.id, billing_address_id=bill_addr.id,
        # copy of the address text, so later address edits don't change old orders
        shipping_address_text=ship_addr.as_text(), billing_address_text=bill_addr.as_text(),
        shipping_method_id=data.shipping_method_id, coupon_code=calc["coupon_code"],
        subtotal=calc["subtotal"], discount_total=calc["discount_total"],
        tax_total=calc["tax_total"], shipping_total=calc["shipping_total"],
        grand_total=calc["grand_total"], tax_breakdown=to_json(calc["tax_breakdown"]),
        idempotency_key=data.idempotency_key, notes=data.notes,
    )
    db.add(order)
    db.flush()

    for ln, (product, variant, qty) in zip(calc["lines"], items):
        variant_id = variant.id if variant else None
        # reserve picks a warehouse that has enough and blocks the units
        wh_id = inv.reserve(db, product.id, variant_id, qty, "order", order.id)
        db.add(OrderItem(
            order_id=order.id, product_id=product.id, variant_id=variant_id,
            warehouse_id=wh_id, product_name=product.name,
            variant_name=variant.name if variant else None, sku=ln["sku"],
            description_snapshot=(product.description or "")[:1000],
            quantity=qty, unit_price=ln["unit_price"], discount_amount=ln["discount"],
            tax_rate=ln["tax_rate"], tax_amount=ln["tax_amount"], line_total=ln["line_total"],
        ))

    coupon = calc["_coupon"]
    if coupon:
        # +1 in a single UPDATE so two people can't both take the last use
        stmt = update(Coupon).where(Coupon.id == coupon.id)
        if coupon.usage_limit is not None:
            stmt = stmt.where(Coupon.used_count < coupon.usage_limit)
        res = db.execute(stmt.values(used_count=Coupon.used_count + 1))
        if res.rowcount == 0:
            raise HTTPException(409, "Coupon usage limit reached")
        db.add(CouponUsage(coupon_id=coupon.id, user_id=user.id, order_id=order.id,
                           discount_amount=calc["coupon_discount"]))

    from app.models import PromotionHistory
    for ln in calc["lines"]:
        if ln["promotion_id"]:
            db.add(PromotionHistory(promotion_id=ln["promotion_id"], action="applied",
                                    details=f"order {order.order_number}, discount {ln['promotion_discount']}",
                                    changed_by=user.id))

    add_history(db, order, None, "pending", user, "Order placed")

    if is_cod:
        # nothing to pay online, confirm straight away
        db.add(CODCollection(order_id=order.id, expected_amount=order.grand_total))
        db.flush()
        db.refresh(order)
        change_status(db, order, "confirmed", None, "COD order auto confirmed")
    else:
        notify_customer(db, user, f"Order {order.order_number} placed",
                        f"Order {order.order_number} for {order.grand_total} is waiting for payment.",
                        "order", "order", order.id, template="order_confirmation")

    # empty the cart
    for ci in list(cart.items):
        db.delete(ci)
    cart.coupon_code = None

    db.commit()
    db.refresh(order)
    return order, True

def refundable_amount(db, order):
    # money we actually received minus refunds already given / in progress
    paid_rows = db.query(Payment).filter(Payment.order_id == order.id, Payment.status == "succeeded").all()
    paid = sum((money(p.amount) for p in paid_rows), Decimal("0"))
    if order.payment_method == "cod":
        cod = db.query(CODCollection).filter(CODCollection.order_id == order.id).first()
        paid = money(cod.collected_amount) if cod else Decimal("0")
    refund_rows = db.query(Refund).filter(Refund.order_id == order.id,
                                          Refund.status.in_(["requested", "processed"])).all()
    used = sum((money(r.amount) for r in refund_rows), Decimal("0"))
    return money(paid - used)


def create_refund(db, order, amount, reason, user, process_now=False):
    can_refund = refundable_amount(db, order)
    amount = money(amount if amount is not None else can_refund)
    if amount <= 0:
        raise HTTPException(400, "Refund amount must be more than zero")
    if amount > can_refund:
        raise HTTPException(400, f"Refund amount exceeds refundable amount ({can_refund})")
    payment = (db.query(Payment).filter(Payment.order_id == order.id, Payment.status == "succeeded")
               .order_by(Payment.id.desc()).first())
    refund = Refund(refund_number=make_number("RFD"), order_id=order.id,
                    payment_id=payment.id if payment else None, amount=amount,
                    refund_type="full" if amount == money(order.grand_total) else "partial",
                    reason=reason, status="requested", requested_by=user.id if user else None)
    db.add(refund)
    db.flush()
    audit(db, user, "refund_requested", "refund", refund.id, None, {"amount": amount, "order": order.id})
    if process_now:
        process_refund(db, refund, user)
    return refund

def process_refund(db, refund, user):
    if refund.status != "requested":
        raise HTTPException(400, f"Refund is already {refund.status}")
    order = db.get(Order, refund.order_id)
    payment = db.get(Payment, refund.payment_id) if refund.payment_id else None

    if payment and payment.gateway in ("stripe", "sandbox"):
        ref, gw_status = gateway.refund_payment(payment.gateway_reference, refund.amount)
        if gw_status not in ("succeeded", "pending"):
            refund.status = "failed"
            refund.note = f"gateway status {gw_status}"
            return refund
        refund.gateway_reference = ref
        payment.refunded_amount = money(payment.refunded_amount) + money(refund.amount)
        db.add(PaymentTransaction(payment_id=payment.id, order_id=order.id, event_type="refund_processed",
                                  status="succeeded", amount=refund.amount, gateway_reference=ref))
    else:
        # cod refunds go back as bank transfer by finance team, we only record it
        refund.gateway_reference = "manual-" + refund.refund_number

    refund.status = "processed"
    refund.processed_by = user.id if user else None
    refund.processed_at = utcnow()

    done = db.query(Refund).filter(Refund.order_id == order.id, Refund.status == "processed").all()
    total_refunded = sum(money(r.amount) for r in done)
    order.payment_status = "refunded" if total_refunded >= money(order.grand_total) else "partially_refunded"

    audit(db, user, "refund_processed", "refund", refund.id, {"status": "requested"},
          {"status": "processed", "amount": refund.amount})
    customer = db.get(User, order.user_id)
    notify_customer(db, customer, "Refund processed",
                    f"Refund of {refund.amount} for order {order.order_number} has been processed.",
                    "refund", "refund", refund.id, template="refund")
    return refund

def cancel_order(db, order, user, reason, is_staff):
    if db.query(OrderCancellation).filter(OrderCancellation.order_id == order.id).first():
        raise HTTPException(400, "Cancellation already recorded for this order")

    # customers can cancel until it is being packed, staff until it ships
    allowed = ["pending", "confirmed", "processing"] if is_staff else ["pending", "confirmed"]
    if order.status not in allowed:
        db.add(OrderCancellation(order_id=order.id, requested_by=user.id, reason=reason, status="rejected",
                                 outcome=f"Order in '{order.status}' state cannot be cancelled"))
        db.commit()
        raise HTTPException(400, f"Order in '{order.status}' state cannot be cancelled")

    change_status(db, order, "cancelled", user, f"Cancelled: {reason}")

    refund = None
    outcome = "Cancelled, stock released"
    if order.payment_status in ("paid", "partially_refunded"):
        # money was taken, give it back
        refund = create_refund(db, order, None, f"Order cancelled: {reason}", user, process_now=True)
        outcome += f", refund {refund.refund_number} {refund.status}"
    elif order.payment_method == "online" and order.payment_status == "pending":
        # nothing captured, just cancel open payment attempts
        for p in db.query(Payment).filter(Payment.order_id == order.id, Payment.status == "pending").all():
            p.status = "cancelled"
        order.payment_status = "cancelled"
    elif order.payment_method == "cod":
        cod = db.query(CODCollection).filter(CODCollection.order_id == order.id).first()
        if cod and cod.status == "pending":
            cod.status = "failed"
            cod.note = "order cancelled"
        order.payment_status = "cancelled"

    canc = OrderCancellation(order_id=order.id, requested_by=user.id, reason=reason, status="approved",
                             outcome=outcome, refund_id=refund.id if refund else None)
    db.add(canc)
    notify_roles(db, [ORDER_MANAGER, ADMIN], "Order cancelled",
                 f"Order {order.order_number} was cancelled. Reason: {reason}", "order", "order", order.id)
    db.commit()
    db.refresh(canc)
    return canc

def mark_payment_succeeded(db, payment, raw=None):
    if payment.status == "succeeded":
        return payment  # duplicate callback, ignore
    if payment.status == "cancelled":
        raise HTTPException(409, "Payment was cancelled")
    payment.status = "succeeded"
    order = db.get(Order, payment.order_id)
    order.payment_status = "paid"
    db.add(PaymentTransaction(payment_id=payment.id, order_id=order.id, event_type="payment_succeeded",
                              status="succeeded", amount=payment.amount,
                              gateway_reference=payment.gateway_reference, raw_payload=raw))
    audit(db, None, "payment_succeeded", "payment", payment.id, {"status": "pending"}, {"status": "succeeded"})
    if order.status == "pending":
        change_status(db, order, "confirmed", None, "Payment received")
    customer = db.get(User, order.user_id)
    notify_customer(db, customer, "Payment successful",
                    f"We received {payment.amount} for order {order.order_number}.",
                    "payment", "payment", payment.id, template="payment_success")
    return payment

def mark_payment_failed(db, payment, reason=None, raw=None):
    if payment.status in ("succeeded", "failed"):
        return payment
    payment.status = "failed"
    payment.failure_reason = reason
    order = db.get(Order, payment.order_id)
    order.payment_status = "failed"
    db.add(PaymentTransaction(payment_id=payment.id, order_id=order.id, event_type="payment_failed",
                              status="failed", amount=payment.amount,
                              gateway_reference=payment.gateway_reference, raw_payload=raw))
    customer = db.get(User, order.user_id)
    notify_customer(db, customer, "Payment failed",
                    f"Payment for order {order.order_number} failed. {reason or ''}".strip(),
                    "payment", "payment", payment.id, template="payment_failed")
    return payment