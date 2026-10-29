# Module 46: Audit Log Management, Module 47: Dashboard and Summary APIs, Module 48: Reports and Analytics
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import event, func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.core.roles import ADMIN, SUPER_ADMIN
from app.core.utils import from_json, money
from app.models import (AuditLog, Exchange, Inventory, LowStockAlert, Order, Payment, Product, ProductVariant,
                        Refund, ReturnRequest, User, Warehouse)
from app.services import report_service as rs

audit_router = APIRouter(prefix="/audit-logs", tags=["46 Audit Log Management"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["47 Dashboard and Summary APIs"])
reports_router = APIRouter(prefix="/reports", tags=["48 Reports and Analytics APIs"])

auditors = require_roles(ADMIN)  # super admin is always allowed by require_roles


# ---------------- 46: audit logs are append only ----------------

def _block_change(mapper, connection, target):
    raise RuntimeError("Audit logs are read-only, they can't be changed or deleted")


# any update / delete of an AuditLog row from the app fails, only inserts are allowed
event.listen(AuditLog, "before_update", _block_change)
event.listen(AuditLog, "before_delete", _block_change)

CATEGORY_PREFIX = {
    "admin": ("user", "role", "supplier", "message_provider", "notification"),
    "product": ("product", "variant", "category", "brand", "attribute"),
    "pricing": ("price", "pricing", "promotion", "coupon", "tax"),
    "inventory": ("inventory", "stock", "transfer", "threshold", "po_", "warehouse"),
    "order": ("order", "checkout", "cancel", "shipment", "delivery", "return", "exchange"),
    "refund": ("refund", "payment", "cod"),
}


def _log_out(log):
    return {"id": log.id, "user_id": log.user_id, "action": log.action, "entity_type": log.entity_type,
            "entity_id": log.entity_id, "old_value": from_json(log.old_value) or log.old_value,
            "new_value": from_json(log.new_value) or log.new_value, "ip_address": log.ip_address,
            "created_at": log.created_at}


def _day_range(q, column, date_from, date_to):
    if date_from:
        q = q.filter(column >= datetime.combine(date_from, time.min))
    if date_to:
        q = q.filter(column < datetime.combine(date_to + timedelta(days=1), time.min))
    return q


@audit_router.get("")
def list_audit_logs(category: Optional[str] = None, action: Optional[str] = None, entity_type: Optional[str] = None,
                    entity_id: Optional[int] = None, user_id: Optional[int] = None,
                    date_from: Optional[date] = None, date_to: Optional[date] = None,
                    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                    user: User = Depends(auditors), db: Session = Depends(get_db)):
    q = db.query(AuditLog)
    if category:
        prefixes = CATEGORY_PREFIX.get(category)
        if not prefixes:
            raise HTTPException(400, f"category must be one of {', '.join(CATEGORY_PREFIX)}")
        q = q.filter(or_(*[AuditLog.action.like(f"{p}%") for p in prefixes],
                         *[AuditLog.entity_type.like(f"{p}%") for p in prefixes]))
    if action:
        q = q.filter(AuditLog.action.like(f"%{action}%"))
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.filter(AuditLog.entity_id == entity_id)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    q = _day_range(q, AuditLog.created_at, date_from, date_to)
    total = q.count()
    logs = q.order_by(AuditLog.id.desc()).offset(skip).limit(limit).all()
    return {"total": total, "skip": skip, "limit": limit, "logs": [_log_out(x) for x in logs]}


@audit_router.get("/summary")
def audit_summary(days: int = Query(7, ge=1, le=365), user: User = Depends(auditors), db: Session = Depends(get_db)):
    since = datetime.combine(date.today() - timedelta(days=days - 1), time.min)
    rows = (db.query(AuditLog.entity_type, func.count(AuditLog.id)).filter(AuditLog.created_at >= since)
            .group_by(AuditLog.entity_type).order_by(func.count(AuditLog.id).desc()).all())
    top_users = (db.query(AuditLog.user_id, func.count(AuditLog.id)).filter(AuditLog.created_at >= since)
                 .group_by(AuditLog.user_id).order_by(func.count(AuditLog.id).desc()).limit(5).all())
    return {"days": days, "by_entity": {e or "-": c for e, c in rows},
            "most_active_users": [{"user_id": u, "actions": c} for u, c in top_users]}


@audit_router.get("/entity/{entity_type}/{entity_id:int}")
def entity_timeline(entity_type: str, entity_id: int, user: User = Depends(auditors), db: Session = Depends(get_db)):
    """full change history of one record, oldest first - e.g. order 5, refund 2"""
    logs = (db.query(AuditLog).filter(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
            .order_by(AuditLog.id).all())
    if not logs:
        raise HTTPException(404, "No audit history for this record")
    return {"entity_type": entity_type, "entity_id": entity_id, "changes": [_log_out(x) for x in logs]}


@audit_router.get("/{log_id:int}")
def get_audit_log(log_id: int, user: User = Depends(auditors), db: Session = Depends(get_db)):
    log = db.get(AuditLog, log_id)
    if not log:
        raise HTTPException(404, "Audit log not found")
    return _log_out(log)


@audit_router.api_route("/{log_id:int}", methods=["PUT", "PATCH", "DELETE"], include_in_schema=False)
def no_changes(log_id: int):
    raise HTTPException(405, "Audit logs are read-only, they can't be changed or deleted")


# ---------------- 47: dashboard ----------------

SECTION_ROLES = {
    "products": (ADMIN, "product_manager", "inventory_manager"),
    "orders": (ADMIN, "order_manager", "delivery_manager", "finance_manager", "support_agent"),
    "revenue": (ADMIN, "finance_manager"),
    "low_stock": (ADMIN, "inventory_manager", "product_manager"),
    "returns": (ADMIN, "order_manager", "finance_manager", "support_agent"),
}


def _can_see(user, section):
    return user.role_name == SUPER_ADMIN or user.role_name in SECTION_ROLES[section]


def section(name):
    def checker(user: User = Depends(get_current_user)):
        if not _can_see(user, name):
            raise HTTPException(403, f"Your role can't see the {name.replace('_', ' ')} dashboard")
        return user
    return checker


def _grouped(db, column, *filters):
    q = db.query(column, func.count()).filter(*filters) if filters else db.query(column, func.count())
    return {k or "-": c for k, c in q.group_by(column).all()}


def products_summary(db):
    inv = db.query(func.coalesce(func.sum(Inventory.quantity), 0), func.coalesce(func.sum(Inventory.reserved), 0)).one()
    value = (db.query(func.coalesce(func.sum(Inventory.quantity * Product.base_price), 0))
             .join(Product, Product.id == Inventory.product_id).scalar())
    return {"products_by_status": _grouped(db, Product.status), "variants": db.query(func.count(ProductVariant.id)).scalar(),
            "warehouses": db.query(func.count(Warehouse.id)).scalar(), "units_in_stock": int(inv[0]),
            "units_reserved": int(inv[1]), "units_available": int(inv[0]) - int(inv[1]), "stock_value": money(value)}


def orders_summary(db):
    today = datetime.combine(date.today(), time.min)
    return {"by_status": _grouped(db, Order.status), "by_payment_method": _grouped(db, Order.payment_method),
            "total_orders": db.query(func.count(Order.id)).scalar(),
            "orders_today": db.query(func.count(Order.id)).filter(Order.created_at >= today).scalar()}


def revenue_summary(db):
    sold = (db.query(func.coalesce(func.sum(Order.grand_total), 0))
            .filter(Order.status.notin_(rs.NOT_SOLD)).scalar())
    collected = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "succeeded").scalar()
    refunded = db.query(func.coalesce(func.sum(Refund.amount), 0)).filter(Refund.status == "processed").scalar()
    return {"order_value": money(sold), "online_collected": money(collected), "refunded": money(refunded),
            "net_collected": money(money(collected) - money(refunded)),
            "payments_by_status": _grouped(db, Payment.status),
            "orders_by_payment_status": _grouped(db, Order.payment_status)}


def low_stock_summary(db):
    rows = (db.query(Inventory, Product.name).join(Product, Product.id == Inventory.product_id)
            .filter(Inventory.quantity - Inventory.reserved <= Inventory.min_threshold).all())
    return {"low_stock_items": len(rows),
            "out_of_stock_items": sum(1 for i, _ in rows if i.quantity - i.reserved <= 0),
            "open_alerts": db.query(func.count(LowStockAlert.id)).filter(LowStockAlert.status == "open").scalar(),
            "items": [{"product": n, "warehouse_id": i.warehouse_id, "available": i.quantity - i.reserved,
                       "threshold": i.min_threshold} for i, n in rows[:20]]}


def returns_summary(db):
    return {"returns_by_status": _grouped(db, ReturnRequest.status),
            "exchanges_by_status": _grouped(db, Exchange.status),
            "refunds_by_status": _grouped(db, Refund.status)}


SECTIONS = {"products": products_summary, "orders": orders_summary, "revenue": revenue_summary,
            "low_stock": low_stock_summary, "returns": returns_summary}


@dashboard_router.get("")
def my_dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """one call - each role gets only the sections it is allowed to see"""
    allowed = [name for name in SECTIONS if _can_see(user, name)]
    if not allowed:
        raise HTTPException(403, "Dashboard is only for staff")
    return {"role": user.role_name, "sections": {name: SECTIONS[name](db) for name in allowed}}


@dashboard_router.get("/products")
def dashboard_products(user: User = Depends(section("products")), db: Session = Depends(get_db)):
    return products_summary(db)


@dashboard_router.get("/orders")
def dashboard_orders(user: User = Depends(section("orders")), db: Session = Depends(get_db)):
    return orders_summary(db)


@dashboard_router.get("/revenue")
def dashboard_revenue(user: User = Depends(section("revenue")), db: Session = Depends(get_db)):
    return revenue_summary(db)


@dashboard_router.get("/low-stock")
def dashboard_low_stock(user: User = Depends(section("low_stock")), db: Session = Depends(get_db)):
    return low_stock_summary(db)


@dashboard_router.get("/returns")
def dashboard_returns(user: User = Depends(section("returns")), db: Session = Depends(get_db)):
    return returns_summary(db)


# ---------------- 48: reports ----------------

def report_filters(date_from: Optional[date] = None, date_to: Optional[date] = None, status: Optional[str] = None,
                   category_id: Optional[int] = None, warehouse_id: Optional[int] = None):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "date_from can't be after date_to")
    return rs.ReportFilter(date_from, date_to, status, category_id, warehouse_id)


def report_access(name):
    def checker(user: User = Depends(get_current_user)):
        if not rs.can_view(user, name):
            raise HTTPException(403, f"Your role can't see the {name} report")
        return user
    return checker


@reports_router.get("/sales")
def sales(f: rs.ReportFilter = Depends(report_filters), user: User = Depends(report_access("sales")),
          db: Session = Depends(get_db)):
    """day wise sales - filters: date, order status, category, warehouse"""
    return rs.run_report(db, "sales", f)


@reports_router.get("/orders")
def orders(f: rs.ReportFilter = Depends(report_filters), user: User = Depends(report_access("orders")),
           db: Session = Depends(get_db)):
    return rs.run_report(db, "orders", f)


@reports_router.get("/inventory")
def inventory(low_only: bool = False, f: rs.ReportFilter = Depends(report_filters),
              user: User = Depends(report_access("inventory")), db: Session = Depends(get_db)):
    return rs.run_report(db, "inventory", f, low_only=low_only)


@reports_router.get("/payments")
def payments(f: rs.ReportFilter = Depends(report_filters), user: User = Depends(report_access("payments")),
             db: Session = Depends(get_db)):
    return rs.run_report(db, "payments", f)


@reports_router.get("/product-performance")
def products(limit: int = Query(20, ge=1, le=100), f: rs.ReportFilter = Depends(report_filters),
             user: User = Depends(report_access("products")), db: Session = Depends(get_db)):
    return rs.run_report(db, "products", f, limit=limit)


@reports_router.get("/category-performance")
def categories(f: rs.ReportFilter = Depends(report_filters), user: User = Depends(report_access("categories")),
               db: Session = Depends(get_db)):
    return rs.run_report(db, "categories", f)
