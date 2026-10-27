# Module 15: Inventory Transfers
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.utils import make_number, utcnow
from app.core.roles import ADMIN, INVENTORY_MANAGER
from app.models import InventoryTransfer, TransferItem, Warehouse, Product, ProductVariant, StockMovement, User
from app.schemas.inventory import TransferCreate, TransferOut, TransferDispatch, TransferReceive, MovementOut
from app.services import inventory_service as inv

router = APIRouter(prefix="/transfers", tags=["15 Inventory Transfers"])

stock_roles = require_roles(ADMIN, INVENTORY_MANAGER)


def _qty_map(items):
    # {item_id: quantity} so we can look up what was sent for each line
    return {i.item_id: i.quantity for i in items}

@router.post("", response_model=TransferOut, status_code=201, summary="Create transfer request")
def create_transfer(data: TransferCreate, db: Session = Depends(get_db), user: User = Depends(stock_roles)):
    src = get_or_404(db, Warehouse, data.from_warehouse_id, "Source warehouse")
    dst = get_or_404(db, Warehouse, data.to_warehouse_id, "Destination warehouse")
    if src.status != "active" or dst.status != "active":
        raise HTTPException(400, "Both warehouses must be active")

    t = InventoryTransfer(transfer_number=make_number("TRF"), from_warehouse_id=src.id, to_warehouse_id=dst.id,
                          note=data.note, requested_by=user.id)
    for item in data.items:
        get_or_404(db, Product, item.product_id)
        if item.variant_id:
            v = db.get(ProductVariant, item.variant_id)
            if not v or v.product_id != item.product_id:
                raise HTTPException(400, f"Variant {item.variant_id} doesn't match product {item.product_id}")
        row = inv.get_row(db, item.product_id, item.variant_id, src.id)
        if not row or row.available < item.quantity:
            raise HTTPException(409, f"Not enough stock of product {item.product_id} in {src.code}")
        t.items.append(TransferItem(product_id=item.product_id, variant_id=item.variant_id,
                                    requested_qty=item.quantity))
    db.add(t)
    db.flush()
    audit(db, user, "transfer_requested", "transfer", t.id, None, {"number": t.transfer_number})
    db.commit()
    db.refresh(t)
    return t


@router.get("", response_model=List[TransferOut])
def list_transfers(status: Optional[str] = None, warehouse_id: Optional[int] = None,
                   db: Session = Depends(get_db), user: User = Depends(stock_roles)):
    q = db.query(InventoryTransfer)
    if status:
        q = q.filter(InventoryTransfer.status == status)
    if warehouse_id:
        q = q.filter((InventoryTransfer.from_warehouse_id == warehouse_id) |
                     (InventoryTransfer.to_warehouse_id == warehouse_id))
    return q.order_by(InventoryTransfer.id.desc()).all()


@router.get("/{transfer_id}", response_model=TransferOut)
def get_transfer(transfer_id: int, db: Session = Depends(get_db), user: User = Depends(stock_roles)):
    return get_or_404(db, InventoryTransfer, transfer_id, "Transfer")

@router.post("/{transfer_id}/dispatch", response_model=TransferOut, summary="Record dispatched quantities")
def dispatch(transfer_id: int, data: TransferDispatch, db: Session = Depends(get_db),
             user: User = Depends(stock_roles)):
    t = get_or_404(db, InventoryTransfer, transfer_id, "Transfer")
    if t.status != "requested":
        raise HTTPException(400, f"Transfer is {t.status}, only requested transfers can be dispatched")
    qty = _qty_map(data.items)
    for item in t.items:
        send = qty.get(item.id, item.requested_qty)
        if send > item.requested_qty:
            raise HTTPException(400, f"Item {item.id}: can't dispatch more than requested")
        if send == 0:
            continue
        row = inv.get_row(db, item.product_id, item.variant_id, t.from_warehouse_id, lock=True)
        if not row:
            raise HTTPException(409, f"Item {item.id}: no stock in source warehouse")
        inv.reduce_stock(db, row, send, "transfer_out", "transfer", t.id, user=user)
        item.dispatched_qty = send
    t.status = "dispatched"
    t.dispatched_at = utcnow()
    audit(db, user, "transfer_dispatched", "transfer", t.id, {"status": "requested"}, {"status": "dispatched"})
    db.commit()
    db.refresh(t)
    return t

@router.post("/{transfer_id}/receive", response_model=TransferOut, summary="Record received quantities")
def receive(transfer_id: int, data: TransferReceive, db: Session = Depends(get_db),
            user: User = Depends(stock_roles)):
    t = get_or_404(db, InventoryTransfer, transfer_id, "Transfer")
    if t.status != "dispatched":
        raise HTTPException(400, "Only dispatched transfers can be received")
    qty = _qty_map(data.items)
    for item in t.items:
        got = qty.get(item.id, item.dispatched_qty)
        if got > item.dispatched_qty:
            raise HTTPException(400, f"Item {item.id}: received more than dispatched")
        if got:
            row = inv.get_row(db, item.product_id, item.variant_id, t.to_warehouse_id, create=True, lock=True)
            inv.add_stock(db, row, got, "transfer_in", "transfer", t.id, user=user)
        item.received_qty = got
    t.status = "received"
    t.received_at = utcnow()
    short = [i.id for i in t.items if i.received_qty < i.dispatched_qty]
    if short:
        t.note = (t.note or "") + f" | short received items: {short}"
    audit(db, user, "transfer_received", "transfer", t.id, {"status": "dispatched"}, {"status": "received"})
    db.commit()
    db.refresh(t)
    return t
@router.post("/{transfer_id}/cancel", response_model=TransferOut)
def cancel(transfer_id: int, db: Session = Depends(get_db), user: User = Depends(stock_roles)):
    t = get_or_404(db, InventoryTransfer, transfer_id, "Transfer")
    if t.status != "requested":
        raise HTTPException(400, "Only requested (not dispatched) transfers can be cancelled")
    t.status = "cancelled"
    audit(db, user, "transfer_cancelled", "transfer", t.id, None, None)
    db.commit()
    db.refresh(t)

    return t


@router.get("/{transfer_id}/movements", response_model=List[MovementOut], summary="Movement history of transfer")
def transfer_movements(transfer_id: int, db: Session = Depends(get_db), user: User = Depends(stock_roles)):
    get_or_404(db, InventoryTransfer, transfer_id, "Transfer")
    return (db.query(StockMovement).filter(StockMovement.reference_type == "transfer",
                                           StockMovement.reference_id == transfer_id)
            .order_by(StockMovement.id).all())
