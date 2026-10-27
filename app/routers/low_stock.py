# Module 16: Low-Stock Management
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, INVENTORY_MANAGER
from app.core.utils import utcnow
from app.models import Inventory, ThresholdHistory, LowStockAlert, Product, Warehouse, User
from app.schemas.inventory import ThresholdUpdate, InventoryOut, ThresholdHistoryOut, AlertOut, LowStockRow
from app.services import inventory_service as inv

router = APIRouter(prefix="/low-stock", tags=["16 Low-Stock Management"])

stock_roles = require_roles(ADMIN, INVENTORY_MANAGER)

@router.put("/thresholds", response_model=InventoryOut,
            summary="Set minimum stock threshold")
def set_threshold(data: ThresholdUpdate,
                  db: Session = Depends(get_db),
                  user: User = Depends(stock_roles)):
    row = get_or_404(db, Inventory, data.inventory_id, "Inventory row")
    db.add(ThresholdHistory(inventory_id=row.id,
                            old_value=row.min_threshold,
                            new_value=data.min_threshold,
                            changed_by=user.id))
    audit(db, user, "threshold_changed", "inventory", row.id,
          {"min_threshold": row.min_threshold},
          {"min_threshold": data.min_threshold})
    row.min_threshold = data.min_threshold
    inv.check_low_stock(db, row)       # new limit may make it low now
    inv.resolve_alert_if_ok(db, row)   # or it's fine again
    db.commit()
    db.refresh(row)
    return row

@router.get("/thresholds/{inventory_id}/history",
            response_model=List[ThresholdHistoryOut])
def threshold_history(inventory_id: int,
                      db: Session = Depends(get_db),
                      user: User = Depends(stock_roles)):
    q = db.query(ThresholdHistory)
    q = q.filter(ThresholdHistory.inventory_id == inventory_id)
    return q.order_by(ThresholdHistory.id.desc()).all()


@router.get("/products", response_model=List[LowStockRow],
            summary="Items at or below threshold")
def low_stock_products(warehouse_id: Optional[int] = None,
                       db: Session = Depends(get_db),
                       user: User = Depends(stock_roles)):
    q = db.query(Inventory).filter(Inventory.min_threshold > 0)
    if warehouse_id:
        q = q.filter(Inventory.warehouse_id == warehouse_id)
    out = []
    for row in q.all():
        if row.available <= row.min_threshold:
            p = db.get(Product, row.product_id)
            w = db.get(Warehouse, row.warehouse_id)
            out.append(LowStockRow(
                inventory_id=row.id, product_id=row.product_id,
                product_name=p.name, variant_id=row.variant_id,
                warehouse_id=row.warehouse_id, warehouse_code=w.code,
                available=row.available, threshold=row.min_threshold,
                shortfall=row.min_threshold - row.available))
    # biggest shortage first
    out.sort(key=lambda r: -r.shortfall)
    return out

@router.post("/scan", summary="Scan all stock and generate restock alerts")
def scan(db: Session = Depends(get_db), user: User = Depends(stock_roles)):
    def open_count(row_id):
        return db.query(LowStockAlert).filter(LowStockAlert.inventory_id == row_id,
                                              LowStockAlert.status == "open").count()
    created = 0
    for row in db.query(Inventory).filter(Inventory.min_threshold > 0).all():
        before = open_count(row.id)
        inv.check_low_stock(db, row)
        db.flush()
        created += open_count(row.id) - before
    db.commit()
    return {"new_alerts": created}


@router.get("/alerts", response_model=List[AlertOut])
def alerts(status: Optional[str] = "open", db: Session = Depends(get_db),
           user: User = Depends(stock_roles)):
    q = db.query(LowStockAlert)
    if status:
        q = q.filter(LowStockAlert.status == status)
    return q.order_by(LowStockAlert.id.desc()).all()

@router.patch("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(alert_id: int, db: Session = Depends(get_db),
                  user: User = Depends(stock_roles)):
    alert = get_or_404(db, LowStockAlert, alert_id, "Alert")
    if alert.status == "resolved":
        raise HTTPException(400, "Already resolved")
    alert.status = "resolved"
    alert.resolved_at = utcnow()
    db.commit()
    db.refresh(alert)
    return alert


@router.get("/report", summary="Low stock report grouped by warehouse")
def report(db: Session = Depends(get_db), user: User = Depends(stock_roles)):
    rows = low_stock_products(None, db, user)
    grouped = {}
    for r in rows:
        grouped.setdefault(r.warehouse_code, []).append(r.model_dump())
    open_alerts = db.query(LowStockAlert).filter(LowStockAlert.status == "open").count()
    return {"total_low_items": len(rows), "open_alerts": open_alerts, "by_warehouse": grouped}