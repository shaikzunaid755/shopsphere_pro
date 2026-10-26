from fastapi import HTTPException

from app.core.roles import INVENTORY_MANAGER, ADMIN
from app.core.utils import utcnow
from app.models import Inventory, StockMovement, LowStockAlert, Warehouse
from app.services.notification_service import notify_roles


def get_row(db, product_id, variant_id, warehouse_id, create=False, lock=False):
    q = db.query(Inventory).filter(Inventory.product_id == product_id,
                                   Inventory.warehouse_id == warehouse_id)
    if variant_id:
        q = q.filter(Inventory.variant_id == variant_id)
    else:
        q = q.filter(Inventory.variant_id.is_(None))
    if lock:
        q = q.with_for_update()  # row lock in mysql so two orders can't take the same stock
    row = q.first()
    if not row and create:
        row = Inventory(product_id=product_id, variant_id=variant_id, warehouse_id=warehouse_id,
                        quantity=0, reserved=0)
        db.add(row)
        db.flush()
    return row


def _log(db, row, mtype, qty, ref_type=None, ref_id=None, note=None, user=None):
    db.add(StockMovement(inventory_id=row.id, product_id=row.product_id, variant_id=row.variant_id,
                         warehouse_id=row.warehouse_id, movement_type=mtype, quantity=qty,
                         reference_type=ref_type, reference_id=ref_id, note=note,
                         created_by=user.id if user else None))


def add_stock(db, row, qty, mtype="addition", ref_type=None, ref_id=None, note=None, user=None):
    if qty <= 0:
        raise HTTPException(400, "Quantity must be more than zero")
    wh = db.get(Warehouse, row.warehouse_id)
    if wh and wh.status != "active":
        raise HTTPException(400, "Warehouse is not active")
    if wh and wh.capacity:
        used = sum(r.quantity for r in db.query(Inventory).filter(Inventory.warehouse_id == wh.id).all())
        if used + qty > wh.capacity:
            raise HTTPException(400, f"Warehouse capacity exceeded ({used}/{wh.capacity} used)")
    row.quantity += qty
    _log(db, row, mtype, qty, ref_type, ref_id, note, user)
    resolve_alert_if_ok(db, row)
    return row


def reduce_stock(db, row, qty, mtype="reduction", ref_type=None, ref_id=None, note=None, user=None):
    if qty <= 0:
        raise HTTPException(400, "Quantity must be more than zero")
    # only free stock can be removed, reserved units belong to orders
    if row.quantity - row.reserved < qty:
        raise HTTPException(409, f"Not enough available stock (available {row.quantity - row.reserved})")
    row.quantity -= qty
    _log(db, row, mtype, -qty, ref_type, ref_id, note, user)
    check_low_stock(db, row)
    return row

def total_available(db, product_id, variant_id=None):
    q = db.query(Inventory).filter(Inventory.product_id == product_id)
    q = q.filter(Inventory.variant_id == variant_id) if variant_id else q.filter(Inventory.variant_id.is_(None))
    rows = q.join(Warehouse, Warehouse.id == Inventory.warehouse_id).filter(Warehouse.status == "active").all()
    return sum(max(r.quantity - r.reserved, 0) for r in rows)


def reserve(db, product_id, variant_id, qty, ref_type=None, ref_id=None, preferred_wh=None):
    """reserve qty from one warehouse that can fully cover it. returns the warehouse id"""
    q = (db.query(Inventory).join(Warehouse, Warehouse.id == Inventory.warehouse_id)
         .filter(Inventory.product_id == product_id, Warehouse.status == "active"))
    q = q.filter(Inventory.variant_id == variant_id) if variant_id else q.filter(Inventory.variant_id.is_(None))
    rows = q.with_for_update().all()
    rows.sort(key=lambda r: (r.warehouse_id != preferred_wh, -(r.quantity - r.reserved)))
    for row in rows:
        if row.quantity - row.reserved >= qty:
            row.reserved += qty
            _log(db, row, "reserve", qty, ref_type, ref_id)
            check_low_stock(db, row)
            return row.warehouse_id
    raise HTTPException(409, f"Insufficient stock for product {product_id}"
                             + (f" variant {variant_id}" if variant_id else ""))


def release(db, product_id, variant_id, warehouse_id, qty, ref_type=None, ref_id=None):
    row = get_row(db, product_id, variant_id, warehouse_id, lock=True)
    if not row:
        return
    take = min(qty, row.reserved)
    row.reserved -= take
    _log(db, row, "release", take, ref_type, ref_id)
    resolve_alert_if_ok(db, row)


def commit_sale(db, product_id, variant_id, warehouse_id, qty, ref_type=None, ref_id=None):
    # stock leaves the warehouse when order ships
    row = get_row(db, product_id, variant_id, warehouse_id, lock=True)
    if not row or row.reserved < qty or row.quantity < qty:
        raise HTTPException(409, "Reserved stock missing for this order item")
    row.reserved -= qty
    row.quantity -= qty
    _log(db, row, "sale", -qty, ref_type, ref_id)


def check_low_stock(db, row):
    if not row.min_threshold:
        return None
    available = row.quantity - row.reserved
    if available > row.min_threshold:
        return None
    open_alert = (db.query(LowStockAlert)
                  .filter(LowStockAlert.inventory_id == row.id, LowStockAlert.status == "open").first())
    if open_alert:
        open_alert.available_qty = available
        return open_alert
    alert = LowStockAlert(inventory_id=row.id, product_id=row.product_id, warehouse_id=row.warehouse_id,
                          available_qty=available, threshold=row.min_threshold)
    db.add(alert)
    notify_roles(db, [INVENTORY_MANAGER, ADMIN], "Low stock alert",
                 f"Product #{row.product_id} in warehouse #{row.warehouse_id} has only {available} left "
                 f"(threshold {row.min_threshold})", "inventory", "inventory", row.id)
    return alert


def resolve_alert_if_ok(db, row):
    if row.quantity - row.reserved > (row.min_threshold or 0):
        alerts = (db.query(LowStockAlert)
                  .filter(LowStockAlert.inventory_id == row.id, LowStockAlert.status == "open").all())
        for a in alerts:
            a.status = "resolved"
            a.resolved_at = utcnow()
