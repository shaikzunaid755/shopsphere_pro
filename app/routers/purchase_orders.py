# Module 44: Purchase Order Management
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN
from app.core.utils import make_number, money, utcnow
from app.models import (Product, ProductVariant, PurchaseOrder, PurchaseOrderItem, Supplier, SupplierInvoice,
                        SupplierProduct, User, Warehouse)
from app.schemas.procurement import POCreate, POReceive, POCancel, POOut
from app.services import inventory_service as inv

router = APIRouter(prefix="/purchase-orders", tags=["44 Purchase Order Management"])

po_staff = require_roles(ADMIN, "inventory_manager")
po_viewers = require_roles(ADMIN, "inventory_manager", "finance_manager")

# draft -> ordered -> partially_received -> received ; draft / ordered can be cancelled
RECEIVABLE = ("ordered", "partially_received")


def _po_out(db, po):
    items = db.query(PurchaseOrderItem).filter(PurchaseOrderItem.po_id == po.id).order_by(PurchaseOrderItem.id).all()
    data = POOut.model_validate(po, from_attributes=True).model_dump(exclude={"items"})
    return POOut(**data, items=items)


def _locked_po(db, po_id):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).with_for_update().first()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    return po


@router.post("", response_model=POOut, status_code=201)
def create_po(data: POCreate, user: User = Depends(po_staff), db: Session = Depends(get_db)):
    supplier = get_or_404(db, Supplier, data.supplier_id, "Supplier")
    if supplier.status != "approved":
        raise HTTPException(400, f"Supplier is {supplier.status}, purchase orders need an approved supplier")
    warehouse = get_or_404(db, Warehouse, data.warehouse_id, "Warehouse")
    if warehouse.status != "active":
        raise HTTPException(400, "Warehouse is not active")
    keys = [(i.product_id, i.variant_id) for i in data.items]
    if len(keys) != len(set(keys)):
        raise HTTPException(400, "Same product is listed twice, put the total quantity in one line")

    po = PurchaseOrder(po_number=make_number("PO"), supplier_id=supplier.id, warehouse_id=warehouse.id,
                       status="draft", expected_date=data.expected_date, note=data.note, total_amount=0,
                       created_by=user.id, created_at=utcnow(), updated_at=utcnow())
    db.add(po)
    db.flush()
    total = Decimal("0")
    for line in data.items:
        product = get_or_404(db, Product, line.product_id, "Product")
        if line.variant_id:
            variant = db.get(ProductVariant, line.variant_id)
            if not variant or variant.product_id != product.id:
                raise HTTPException(400, f"Variant {line.variant_id} does not belong to {product.name}")
        link = (db.query(SupplierProduct)
                .filter(SupplierProduct.supplier_id == supplier.id, SupplierProduct.product_id == product.id).first())
        if not link:
            raise HTTPException(400, f"{product.name} is not supplied by {supplier.name}, link it first")
        cost = money(line.unit_cost or link.cost_price)
        db.add(PurchaseOrderItem(po_id=po.id, product_id=product.id, variant_id=line.variant_id,
                                 ordered_qty=line.quantity, received_qty=0, unit_cost=cost))
        total += cost * line.quantity
    po.total_amount = money(total)
    audit(db, user, "po_created", "purchase_order", po.id, None,
          {"supplier": supplier.id, "total": str(po.total_amount), "lines": len(data.items)})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.get("", response_model=List[POOut])
def list_pos(status: Optional[str] = None, supplier_id: Optional[int] = None, warehouse_id: Optional[int] = None,
             skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
             user: User = Depends(po_viewers), db: Session = Depends(get_db)):
    q = db.query(PurchaseOrder)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    if supplier_id:
        q = q.filter(PurchaseOrder.supplier_id == supplier_id)
    if warehouse_id:
        q = q.filter(PurchaseOrder.warehouse_id == warehouse_id)
    return [_po_out(db, po) for po in q.order_by(PurchaseOrder.id.desc()).offset(skip).limit(limit).all()]


@router.get("/{po_id:int}", response_model=POOut)
def get_po(po_id: int, user: User = Depends(po_viewers), db: Session = Depends(get_db)):
    return _po_out(db, get_or_404(db, PurchaseOrder, po_id, "Purchase order"))


@router.post("/{po_id:int}/submit", response_model=POOut)
def submit_po(po_id: int, user: User = Depends(po_staff), db: Session = Depends(get_db)):
    """draft is sent to the supplier"""
    po = _locked_po(db, po_id)
    if po.status != "draft":
        raise HTTPException(400, f"Purchase order is already {po.status}")
    supplier = db.get(Supplier, po.supplier_id)
    if supplier.status != "approved":
        raise HTTPException(400, f"Supplier is {supplier.status} now, can't order")
    po.status = "ordered"
    po.updated_at = utcnow()
    audit(db, user, "po_ordered", "purchase_order", po.id, {"status": "draft"}, {"status": "ordered"})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.post("/{po_id:int}/cancel", response_model=POOut)
def cancel_po(po_id: int, data: POCancel, user: User = Depends(po_staff), db: Session = Depends(get_db)):
    po = _locked_po(db, po_id)
    if po.status not in ("draft", "ordered"):
        raise HTTPException(400, f"Purchase order is {po.status}, received stock can't be cancelled")
    old = po.status
    po.status = "cancelled"
    po.note = f"Cancelled: {data.reason}"
    po.updated_at = utcnow()
    audit(db, user, "po_cancelled", "purchase_order", po.id, {"status": old},
          {"status": "cancelled", "reason": data.reason})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.post("/{po_id:int}/receive", response_model=POOut)
def receive_stock(po_id: int, data: POReceive, user: User = Depends(po_staff), db: Session = Depends(get_db)):
    """goods arrived at the warehouse, stock goes up for what really came"""
    po = _locked_po(db, po_id)
    if po.status not in RECEIVABLE:
        raise HTTPException(400, f"Purchase order is {po.status}, stock can't be received")
    ids = [line.item_id for line in data.items]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "Same item is listed twice")
    items = {i.id: i for i in db.query(PurchaseOrderItem).filter(PurchaseOrderItem.po_id == po.id)
             .with_for_update().all()}
    # check every line first, then change stock - all or nothing
    for line in data.items:
        item = items.get(line.item_id)
        if not item:
            raise HTTPException(400, f"Item {line.item_id} is not part of this purchase order")
        pending = item.ordered_qty - item.received_qty
        if line.quantity > pending:
            raise HTTPException(400, f"Item {item.id}: only {pending} more can be received")

    received = []
    for line in data.items:
        item = items[line.item_id]
        row = inv.get_row(db, item.product_id, item.variant_id, po.warehouse_id, create=True, lock=True)
        inv.add_stock(db, row, line.quantity, mtype="addition", ref_type="purchase_order", ref_id=po.id,
                      note=f"Received against {po.po_number}", user=user)
        item.received_qty += line.quantity
        received.append({"item": item.id, "qty": line.quantity})

    done = all(i.received_qty >= i.ordered_qty for i in items.values())
    old = po.status
    po.status = "received" if done else "partially_received"
    po.updated_at = utcnow()
    audit(db, user, "po_received", "purchase_order", po.id, {"status": old},
          {"status": po.status, "received": received, "note": data.note})
    db.commit()
    db.refresh(po)
    return _po_out(db, po)


@router.get("/suppliers/{supplier_id:int}/history")
def supplier_purchase_history(supplier_id: int, user: User = Depends(po_viewers), db: Session = Depends(get_db)):
    supplier = get_or_404(db, Supplier, supplier_id, "Supplier")
    pos = (db.query(PurchaseOrder).filter(PurchaseOrder.supplier_id == supplier.id)
           .order_by(PurchaseOrder.id.desc()).all())
    rows, ordered_total, received_total = [], Decimal("0"), Decimal("0")
    for po in pos:
        items = db.query(PurchaseOrderItem).filter(PurchaseOrderItem.po_id == po.id).all()
        received_value = sum((money(i.unit_cost) * i.received_qty for i in items), Decimal("0"))
        invoiced = (db.query(func.coalesce(func.sum(SupplierInvoice.total_amount), 0))
                    .filter(SupplierInvoice.po_id == po.id).scalar())
        if po.status != "cancelled":
            ordered_total += money(po.total_amount)
        received_total += received_value
        rows.append({"po_id": po.id, "po_number": po.po_number, "status": po.status,
                     "created_at": po.created_at, "ordered_value": money(po.total_amount),
                     "received_value": money(received_value), "invoiced": money(invoiced)})
    return {"supplier_id": supplier.id, "supplier": supplier.name, "purchase_orders": len(pos),
            "total_ordered": money(ordered_total), "total_received": money(received_total), "orders": rows}
