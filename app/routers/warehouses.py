# Module 14: Warehouse Management
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN, INVENTORY_MANAGER
from app.models import Warehouse, Inventory, User
from app.schemas.inventory import (WarehouseCreate, WarehouseUpdate, WarehouseOut, WarehouseStatus,
                                   WarehouseUsage, InventoryOut)

router = APIRouter(prefix="/warehouses", tags=["14 Warehouse Management"])

# warehouse data is internal, only inventory people and admins see it
wh_roles = require_roles(ADMIN, INVENTORY_MANAGER)


def _usage(db, wh):
    used = sum(r.quantity for r in db.query(Inventory).filter(Inventory.warehouse_id == wh.id).all())
    data = WarehouseOut.model_validate(wh).model_dump()
    return WarehouseUsage(**data, used_units=used, free_units=(wh.capacity - used) if wh.capacity else None)

@router.post("", response_model=WarehouseOut, status_code=201)
def create_warehouse(data: WarehouseCreate, db: Session = Depends(get_db), user: User = Depends(wh_roles)):
    code = data.code.upper()
    if db.query(Warehouse).filter(Warehouse.code == code).first():
        raise HTTPException(409, "Warehouse code already exists")
    wh = Warehouse(**data.model_dump())
    wh.code = code
    db.add(wh)
    db.flush()
    audit(db, user, "warehouse_created", "warehouse", wh.id, None, data.model_dump())
    db.commit()
    db.refresh(wh)
    return wh

@router.get("", response_model=List[WarehouseUsage])
def list_warehouses(db: Session = Depends(get_db), user: User = Depends(wh_roles)):
    return [_usage(db, w) for w in db.query(Warehouse).order_by(Warehouse.code).all()]


@router.get("/{warehouse_id}", response_model=WarehouseUsage)
def get_warehouse(warehouse_id: int, db: Session = Depends(get_db), user: User = Depends(wh_roles)):
    return _usage(db, get_or_404(db, Warehouse, warehouse_id))

@router.put("/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(warehouse_id: int, data: WarehouseUpdate, db: Session = Depends(get_db),
                     user: User = Depends(wh_roles)):
    wh = get_or_404(db, Warehouse, warehouse_id)
    changes = data.model_dump(exclude_unset=True)
    if "capacity" in changes and changes["capacity"]:
        used = sum(r.quantity for r in db.query(Inventory).filter(Inventory.warehouse_id == wh.id).all())
        if changes["capacity"] < used:
            raise HTTPException(400, f"Capacity can't be lower than current stock ({used})")
    old = {k: getattr(wh, k) for k in changes}
    for k, v in changes.items():
        setattr(wh, k, v)
    audit(db, user, "warehouse_updated", "warehouse", wh.id, old, changes)
    db.commit()
    db.refresh(wh)
    return wh

@router.patch("/{warehouse_id}/status", response_model=WarehouseOut)
def warehouse_status(warehouse_id: int, data: WarehouseStatus, db: Session = Depends(get_db),
                     user: User = Depends(wh_roles)):
    wh = get_or_404(db, Warehouse, warehouse_id)
    if data.status == "inactive":
        reserved = sum(r.reserved for r in db.query(Inventory).filter(Inventory.warehouse_id == wh.id).all())
        if reserved:
            raise HTTPException(400, f"{reserved} units are reserved for open orders, can't deactivate")
    old = wh.status
    wh.status = data.status
    audit(db, user, "warehouse_status", "warehouse", wh.id, {"status": old}, {"status": data.status})
    db.commit()
    db.refresh(wh)
    return wh


@router.get("/{warehouse_id}/stock", response_model=List[InventoryOut], summary="Warehouse level stock")
def warehouse_stock(warehouse_id: int, db: Session = Depends(get_db), user: User = Depends(wh_roles)):
    get_or_404(db, Warehouse, warehouse_id)
    return db.query(Inventory).filter(Inventory.warehouse_id == warehouse_id).all()