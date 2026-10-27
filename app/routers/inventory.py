# Module 13: Inventory Management
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, INVENTORY_MANAGER, PRODUCT_MANAGER, ORDER_MANAGER
from app.models import Inventory, StockMovement, Product, ProductVariant, Warehouse, User
from app.schemas.inventory import StockChange, InventoryOut, MovementOut
from app.services import inventory_service as inv

router = APIRouter(prefix="/inventory", tags=["13 Inventory Management"])

stock_editors = require_roles(ADMIN, INVENTORY_MANAGER)
stock_viewers = require_roles(ADMIN, INVENTORY_MANAGER, PRODUCT_MANAGER, ORDER_MANAGER)


def _validate(db, data: StockChange):
    product = get_or_404(db, Product, data.product_id)
    if data.variant_id:
        v = db.get(ProductVariant, data.variant_id)
        if not v or v.product_id != product.id:
            raise HTTPException(400, "Variant does not belong to product")
    elif product.variants:
        raise HTTPException(400, "This product has variants, pass variant_id")
    get_or_404(db, Warehouse, data.warehouse_id)


@router.post("/add", response_model=InventoryOut, summary="Record stock addition")
def add_stock(data: StockChange, db: Session = Depends(get_db), user: User = Depends(stock_editors)):
    _validate(db, data)
    row = inv.get_row(db, data.product_id, data.variant_id, data.warehouse_id, create=True, lock=True)
    old = row.quantity
    inv.add_stock(db, row, data.quantity, "addition", note=data.note, user=user)
    audit(db, user, "stock_added", "inventory", row.id, {"quantity": old}, {"quantity": row.quantity})
    db.commit()
    db.refresh(row)
    return row


@router.post("/reduce", response_model=InventoryOut, summary="Record stock reduction (damage, loss...)")
def reduce_stock(data: StockChange, db: Session = Depends(get_db), user: User = Depends(stock_editors)):
    _validate(db, data)
    row = inv.get_row(db, data.product_id, data.variant_id, data.warehouse_id, lock=True)
    if not row:
        raise HTTPException(404, "No stock for this item in this warehouse")
    old = row.quantity
    inv.reduce_stock(db, row, data.quantity, "reduction", note=data.note, user=user)
    audit(db, user, "stock_reduced", "inventory", row.id, {"quantity": old}, {"quantity": row.quantity})
    db.commit()
    db.refresh(row)
    return row


@router.get("", response_model=List[InventoryOut])
def list_stock(product_id: Optional[int] = None, variant_id: Optional[int] = None,
               warehouse_id: Optional[int] = None, db: Session = Depends(get_db),
               user: User = Depends(stock_viewers)):
    q = db.query(Inventory)
    if product_id:
        q = q.filter(Inventory.product_id == product_id)
    if variant_id:
        q = q.filter(Inventory.variant_id == variant_id)
    if warehouse_id:
        q = q.filter(Inventory.warehouse_id == warehouse_id)
    return q.all()


@router.get("/products/{product_id}", summary="Stock of a product across warehouses (and per variant)")
def product_stock(product_id: int, variant_id: Optional[int] = None, db: Session = Depends(get_db),
                  user: User = Depends(stock_viewers)):
    get_or_404(db, Product, product_id)
    q = db.query(Inventory).filter(Inventory.product_id == product_id)
    if variant_id:
        q = q.filter(Inventory.variant_id == variant_id)
    rows = q.all()
    return {
        "product_id": product_id,
        "variant_id": variant_id,
        "total_quantity": sum(r.quantity for r in rows),
        "total_reserved": sum(r.reserved for r in rows),
        "total_available": sum(r.available for r in rows),
        "warehouses": [InventoryOut.model_validate(r) for r in rows],
    }


@router.get("/movements", response_model=List[MovementOut])
def movements(product_id: Optional[int] = None, warehouse_id: Optional[int] = None,
              movement_type: Optional[str] = None, limit: int = Query(100, le=500),
              db: Session = Depends(get_db), user: User = Depends(stock_viewers)):
    q = db.query(StockMovement)
    if product_id:
        q = q.filter(StockMovement.product_id == product_id)
    if warehouse_id:
        q = q.filter(StockMovement.warehouse_id == warehouse_id)
    if movement_type:
        q = q.filter(StockMovement.movement_type == movement_type)
    return q.order_by(StockMovement.id.desc()).limit(limit).all()


@router.get("/{inventory_id}", response_model=InventoryOut)
def get_stock_row(inventory_id: int, db: Session = Depends(get_db), user: User = Depends(stock_viewers)):
    return get_or_404(db, Inventory, inventory_id, "Inventory row")