"""report queries used by Module 48 (json reports) and Module 49 (csv / excel / pdf export)"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from app.core.utils import money
from app.models import Category, Inventory, Order, OrderItem, Payment, Product, Refund, Warehouse

NOT_SOLD = ("cancelled", "pending")  # these orders don't count as sales


@dataclass
class ReportFilter:
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    status: Optional[str] = None
    category_id: Optional[int] = None
    warehouse_id: Optional[int] = None


def _dates(q, column, f):
    if f.date_from:
        q = q.filter(column >= datetime.combine(f.date_from, time.min))
    if f.date_to:
        q = q.filter(column < datetime.combine(f.date_to + timedelta(days=1), time.min))
    return q


def _result(title, columns, rows, totals=None):
    return {"title": title, "columns": columns, "rows": rows, "totals": totals or {}, "count": len(rows)}


def _items_query(db, f, *cols):
    """order lines joined with order + product, all filters applied"""
    q = (db.query(*cols).select_from(OrderItem).join(Order, Order.id == OrderItem.order_id)
         .join(Product, Product.id == OrderItem.product_id))
    q = _dates(q, Order.created_at, f)
    q = q.filter(Order.status == f.status) if f.status else q.filter(Order.status.notin_(NOT_SOLD))
    if f.category_id:
        q = q.filter(Product.category_id == f.category_id)
    if f.warehouse_id:
        q = q.filter(OrderItem.warehouse_id == f.warehouse_id)
    return q


def sales_report(db, f: ReportFilter):
    day = func.date(Order.created_at)
    rows = (_items_query(db, f, day, func.count(func.distinct(Order.id)), func.sum(OrderItem.quantity),
                         func.sum(OrderItem.discount_amount), func.sum(OrderItem.tax_amount),
                         func.sum(OrderItem.line_total))
            .group_by(day).order_by(day).all())
    out, totals = [], {"orders": 0, "units": 0, "discount": Decimal("0"), "tax": Decimal("0"), "sales": Decimal("0")}
    for d, orders, units, disc, tax, sales in rows:
        out.append({"date": str(d), "orders": orders, "units": int(units or 0), "discount": money(disc),
                    "tax": money(tax), "sales": money(sales)})
        totals["orders"] += orders
        totals["units"] += int(units or 0)
        totals["discount"] += money(disc)
        totals["tax"] += money(tax)
        totals["sales"] += money(sales)
    return _result("Sales report", ["date", "orders", "units", "discount", "tax", "sales"], out, totals)


def order_report(db, f: ReportFilter):
    q = _dates(db.query(Order.status, Order.payment_method, func.count(Order.id), func.sum(Order.grand_total)),
               Order.created_at, f)
    if f.status:
        q = q.filter(Order.status == f.status)
    rows = q.group_by(Order.status, Order.payment_method).order_by(Order.status).all()
    out = [{"status": s, "payment_method": m, "orders": c, "value": money(v)} for s, m, c, v in rows]
    total_orders = sum(r["orders"] for r in out)
    total_value = sum((r["value"] for r in out), Decimal("0"))
    avg = money(total_value / total_orders) if total_orders else money(0)
    return _result("Order report", ["status", "payment_method", "orders", "value"], out,
                   {"orders": total_orders, "value": money(total_value), "average_order_value": avg})


def inventory_report(db, f: ReportFilter, low_only=False):
    q = (db.query(Inventory, Product.name, Product.base_price, Warehouse.name, Category.name)
         .join(Product, Product.id == Inventory.product_id)
         .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
         .outerjoin(Category, Category.id == Product.category_id))
    if f.warehouse_id:
        q = q.filter(Inventory.warehouse_id == f.warehouse_id)
    if f.category_id:
        q = q.filter(Product.category_id == f.category_id)
    if low_only:
        q = q.filter(Inventory.quantity - Inventory.reserved <= Inventory.min_threshold)
    out, value_total, units_total = [], Decimal("0"), 0
    for inv, pname, price, wname, cname in q.order_by(Product.name, Warehouse.name).all():
        available = inv.quantity - inv.reserved
        value = money(price) * inv.quantity
        out.append({"product": pname, "variant_id": inv.variant_id, "category": cname or "-", "warehouse": wname,
                    "quantity": inv.quantity, "reserved": inv.reserved, "available": available,
                    "threshold": inv.min_threshold or 0, "low_stock": available <= (inv.min_threshold or 0),
                    "stock_value": money(value)})
        value_total += value
        units_total += inv.quantity
    return _result("Inventory report", ["product", "variant_id", "category", "warehouse", "quantity", "reserved",
                                        "available", "threshold", "low_stock", "stock_value"], out,
                   {"units": units_total, "stock_value": money(value_total),
                    "low_stock_rows": sum(1 for r in out if r["low_stock"])})


def payment_report(db, f: ReportFilter):
    q = _dates(db.query(Payment.method, Payment.status, func.count(Payment.id), func.sum(Payment.amount)),
               Payment.created_at, f)
    if f.status:
        q = q.filter(Payment.status == f.status)
    pay = [{"type": "payment", "method": m, "status": s, "count": c, "amount": money(a)}
           for m, s, c, a in q.group_by(Payment.method, Payment.status).all()]
    rq = _dates(db.query(Refund.refund_type, Refund.status, func.count(Refund.id), func.sum(Refund.amount)),
                Refund.created_at, f)
    ref = [{"type": "refund", "method": t, "status": s, "count": c, "amount": money(a)}
           for t, s, c, a in rq.group_by(Refund.refund_type, Refund.status).all()]
    collected = sum((r["amount"] for r in pay if r["status"] == "succeeded"), Decimal("0"))
    refunded = sum((r["amount"] for r in ref if r["status"] == "processed"), Decimal("0"))
    return _result("Payment and refund report", ["type", "method", "status", "count", "amount"], pay + ref,
                   {"collected": money(collected), "refunded": money(refunded), "net": money(collected - refunded)})


def product_performance(db, f: ReportFilter, limit=20):
    units = func.sum(OrderItem.quantity)
    rows = (_items_query(db, f, Product.id, Product.name, units, func.count(func.distinct(Order.id)),
                         func.sum(OrderItem.line_total))
            .group_by(Product.id, Product.name).order_by(units.desc()).limit(limit).all())
    out = [{"product_id": pid, "product": name, "units_sold": int(u or 0), "orders": o, "revenue": money(r)}
           for pid, name, u, o, r in rows]
    return _result("Product performance", ["product_id", "product", "units_sold", "orders", "revenue"], out,
                   {"revenue": money(sum((r["revenue"] for r in out), Decimal("0")))})


def category_performance(db, f: ReportFilter):
    rows = (_items_query(db, f, Category.name, func.count(func.distinct(Product.id)), func.sum(OrderItem.quantity),
                         func.sum(OrderItem.line_total))
            .outerjoin(Category, Category.id == Product.category_id)
            .group_by(Category.name).order_by(func.sum(OrderItem.line_total).desc()).all())
    out = [{"category": c or "Uncategorised", "products_sold": p, "units_sold": int(u or 0), "revenue": money(r)}
           for c, p, u, r in rows]
    return _result("Category performance", ["category", "products_sold", "units_sold", "revenue"], out,
                   {"revenue": money(sum((r["revenue"] for r in out), Decimal("0")))})


REPORTS = {
    "sales": sales_report,
    "orders": order_report,
    "inventory": inventory_report,
    "payments": payment_report,
    "products": product_performance,
    "categories": category_performance,
}

# who can see which report (super admin always can)
REPORT_ROLES = {
    "sales": ("admin", "finance_manager", "order_manager", "product_manager"),
    "orders": ("admin", "finance_manager", "order_manager"),
    "inventory": ("admin", "inventory_manager", "product_manager"),
    "payments": ("admin", "finance_manager"),
    "products": ("admin", "product_manager", "order_manager", "finance_manager"),
    "categories": ("admin", "product_manager", "order_manager", "finance_manager"),
}


def can_view(user, name):
    return user.role_name == "super_admin" or user.role_name in REPORT_ROLES[name]


def run_report(db, name, f, **extra):
    if name == "inventory":
        return inventory_report(db, f, low_only=extra.get("low_only", False))
    if name == "products":
        return product_performance(db, f, limit=extra.get("limit", 20))
    return REPORTS[name](db, f)
