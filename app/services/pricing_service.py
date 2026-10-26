from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import or_

from app.core.config import settings
from app.core.utils import money, utcnow
from app.models import (Product, ProductVariant, TaxRule, Promotion, Coupon, CouponUsage,
                        ShippingMethod, Category)
from app.services.inventory_service import total_available


def unit_price(product: Product, variant: ProductVariant = None):
    if variant and variant.price is not None:
        return money(variant.price)
    return money(product.current_price())


def category_chain(db, category_id):
    # the category and all its parents, so a promo on "Clothing" also hits "T-Shirts"
    ids = []
    seen = set()
    cat = db.get(Category, category_id) if category_id else None
    while cat and cat.id not in seen:
        ids.append(cat.id)
        seen.add(cat.id)
        cat = db.get(Category, cat.parent_id) if cat.parent_id else None
    return ids


def tax_rule_for(db, product):
    rule = (db.query(TaxRule).filter(TaxRule.is_active == True, TaxRule.product_id == product.id)  # noqa
            .first())
    if rule:
        return rule
    for cid in category_chain(db, product.category_id):
        rule = (db.query(TaxRule)
                .filter(TaxRule.is_active == True, TaxRule.category_id == cid, TaxRule.product_id.is_(None))  # noqa
                .first())
        if rule:
            return rule
    return (db.query(TaxRule)
            .filter(TaxRule.is_active == True, TaxRule.product_id.is_(None), TaxRule.category_id.is_(None))  # noqa
            .first())


def calc_discount(discount_type, value, amount, cap=None):
    amount = money(amount)
    if discount_type == "percentage":
        d = amount * Decimal(str(value)) / Decimal("100")
    else:
        d = Decimal(str(value))
    if cap is not None:
        d = min(d, Decimal(str(cap)))
    return money(min(d, amount))


def best_promotion(db, product, qty, line_amount, cart_subtotal):
    now = utcnow()
    cats = category_chain(db, product.category_id)
    promos = (db.query(Promotion)
              .filter(Promotion.is_active == True, Promotion.start_date <= now, Promotion.end_date >= now)  # noqa
              .filter(or_(Promotion.product_id == product.id, Promotion.category_id.in_(cats or [0])))
              .all())
    best, best_amount = None, Decimal("0")
    for p in promos:
        if qty < (p.min_quantity or 1):
            continue
        if cart_subtotal < money(p.min_order_amount):
            continue
        d = calc_discount(p.discount_type, p.discount_value, line_amount)
        # fixed promos are per unit
        if p.discount_type == "fixed":
            d = money(min(Decimal(str(p.discount_value)) * qty, line_amount))
        if d > best_amount:
            best, best_amount = p, d
    return best, best_amount


def validate_coupon(db, code, user_id, amount):
    coupon = db.query(Coupon).filter(Coupon.code == code.upper().strip()).first()
    if not coupon or not coupon.is_active:
        raise HTTPException(400, "Invalid coupon code")
    now = utcnow()
    if now < coupon.valid_from or now > coupon.valid_to:
        raise HTTPException(400, "Coupon is expired or not yet valid")
    if amount < money(coupon.min_purchase):
        raise HTTPException(400, f"Minimum purchase of {coupon.min_purchase} needed for this coupon")
    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        raise HTTPException(400, "Coupon usage limit reached")
    used_by_user = (db.query(CouponUsage)
                    .filter(CouponUsage.coupon_id == coupon.id, CouponUsage.user_id == user_id).count())
    if coupon.per_user_limit and used_by_user >= coupon.per_user_limit:
        raise HTTPException(400, "You have already used this coupon")
    discount = calc_discount(coupon.discount_type, coupon.discount_value, amount, coupon.max_discount)
    return coupon, discount


def shipping_methods_for(db, amount, state=None, cod=False):
    methods = db.query(ShippingMethod).filter(ShippingMethod.is_active == True).all()  # noqa
    result = []
    for m in methods:
        ok, _ = is_method_eligible(m, amount, state, cod)
        if ok:
            result.append(m)
    return result


def is_method_eligible(method, amount, state=None, cod=False):
    if not method.is_active:
        return False, "Shipping method is inactive"
    if amount < money(method.min_order_amount):
        return False, f"Minimum order {method.min_order_amount} for {method.name}"
    if method.max_order_amount is not None and amount > money(method.max_order_amount):
        return False, f"{method.name} not available above {method.max_order_amount}"
    if method.allowed_states and state:
        allowed = [s.strip().lower() for s in method.allowed_states.split(",") if s.strip()]
        if allowed and state.strip().lower() not in allowed:
            return False, f"{method.name} does not deliver to {state}"
    if cod and not method.cod_supported:
        return False, f"{method.name} does not support cash on delivery"
    return True, None


def shipping_charge(method, amount, item_count):
    free_limit = method.free_above if method.free_above is not None else settings.FREE_SHIPPING_ABOVE
    if free_limit is not None and amount >= money(free_limit):
        return money(0)
    return money(Decimal(str(method.base_charge or 0)) + Decimal(str(method.per_item_charge or 0)) * item_count)


def calculate(db, user_id, items, coupon_code=None, shipping_method_id=None, state=None, cod=False,
              check_stock=True):
    """items: list of (product, variant, qty). everything is recomputed from db prices,
    client never sends prices."""
    lines = []
    errors = []
    raw_subtotal = Decimal("0")
    for product, variant, qty in items:
        raw_subtotal += unit_price(product, variant) * qty


    subtotal = Decimal("0")
    promo_total = Decimal("0")
    for product, variant, qty in items:
        price = unit_price(product, variant)
        line_amount = money(price * qty)
        promo, promo_disc = best_promotion(db, product, qty, line_amount, raw_subtotal)
        available = total_available(db, product.id, variant.id if variant else None)
        if product.status != "active" or (variant and variant.status != "active"):
            errors.append(f"{product.name} is not available anymore")
        elif check_stock and available < qty:
            errors.append(f"Only {available} left for {product.name}")
        subtotal += line_amount
        promo_total += promo_disc
        lines.append({
            "product_id": product.id,
            "variant_id": variant.id if variant else None,
            "name": product.name,
            "variant_name": variant.name if variant else None,
            "sku": variant.sku if variant else product.sku,
            "quantity": qty,
            "unit_price": price,
            "line_amount": line_amount,
            "promotion_id": promo.id if promo else None,
            "promotion_discount": promo_disc,
            "coupon_discount": Decimal("0"),
            "available_stock": available,
        })

    after_promo = subtotal - promo_total
    coupon_disc = Decimal("0")
    coupon = None
    if coupon_code:
        coupon, coupon_disc = validate_coupon(db, coupon_code, user_id, after_promo)
        # spread the coupon over lines so tax is charged on what customer actually pays
        remaining = coupon_disc
        for i, ln in enumerate(lines):
            base = ln["line_amount"] - ln["promotion_discount"]
            if i == len(lines) - 1:
                share = remaining
            else:
                share = money(coupon_disc * base / after_promo) if after_promo > 0 else Decimal("0")
            share = min(share, base)
            ln["coupon_discount"] = share
            remaining -= share

    tax_total = Decimal("0")
    tax_breakdown = {}
    for ln in lines:
        product = next(p for p, v, q in items if p.id == ln["product_id"])
        taxable = ln["line_amount"] - ln["promotion_discount"] - ln["coupon_discount"]
        rule = tax_rule_for(db, product)
        rate = Decimal(str(rule.rate)) if rule else Decimal("0")
        tax = money(taxable * rate / Decimal("100"))
        ln["discount"] = ln["promotion_discount"] + ln["coupon_discount"]
        ln["tax_rate"] = rate
        ln["tax_amount"] = tax
        ln["line_total"] = money(taxable + tax)
        tax_total += tax
        if rule:
            key = f"{rule.name} ({rate}%)"
            tax_breakdown[key] = money(tax_breakdown.get(key, 0) + tax)

    discounted = subtotal - promo_total - coupon_disc
    shipping = Decimal("0")
    method = None
    if shipping_method_id:
        method = db.get(ShippingMethod, shipping_method_id)
        if not method:
            raise HTTPException(404, "Shipping method not found")
        ok, reason = is_method_eligible(method, discounted, state, cod)
        if not ok:
            raise HTTPException(400, reason)
        shipping = shipping_charge(method, discounted, sum(q for _, _, q in items))

    return {
        "lines": lines,
        "item_count": sum(q for _, _, q in items),
        "subtotal": money(subtotal),
        "promotion_discount": money(promo_total),
        "coupon_code": coupon.code if coupon else None,
        "coupon_discount": money(coupon_disc),
        "discount_total": money(promo_total + coupon_disc),
        "tax_total": money(tax_total),
        "tax_breakdown": [{"tax": k, "amount": v} for k, v in tax_breakdown.items()],
        "shipping_method": method.name if method else None,
        "shipping_total": money(shipping),
        "grand_total": money(discounted + tax_total + shipping),
        "errors": errors,
        "_coupon": coupon,
    }