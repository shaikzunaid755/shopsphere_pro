# Module 45: Supplier Invoice Management
from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN
from app.core.utils import money, utcnow
from app.models import PurchaseOrder, PurchaseOrderItem, Supplier, SupplierInvoice, User
from app.schemas.procurement import InvoiceCreate, InvoicePayment, InvoiceOut

router = APIRouter(prefix="/supplier-invoices", tags=["45 Supplier Invoice Management"])

finance = require_roles(ADMIN, "finance_manager")
invoice_viewers = require_roles(ADMIN, "finance_manager", "inventory_manager")
TOLERANCE = Decimal("1.00")  # rounding difference allowed between bill and our numbers


def _received_value(db, po_id):
    items = db.query(PurchaseOrderItem).filter(PurchaseOrderItem.po_id == po_id).all()
    return money(sum((money(i.unit_cost) * i.received_qty for i in items), Decimal("0")))


def _invoiced_amount(db, po_id):
    total = (db.query(func.coalesce(func.sum(SupplierInvoice.amount), 0))
             .filter(SupplierInvoice.po_id == po_id, SupplierInvoice.payment_status != "cancelled").scalar())
    return money(total)


@router.post("", response_model=InvoiceOut, status_code=201)
def record_invoice(data: InvoiceCreate, user: User = Depends(finance), db: Session = Depends(get_db)):
    number = data.invoice_number.strip().upper()
    supplier = get_or_404(db, Supplier, data.supplier_id, "Supplier")
    # same bill entered twice
    if db.query(SupplierInvoice).filter(SupplierInvoice.supplier_id == supplier.id,
                                        SupplierInvoice.invoice_number == number).first():
        raise HTTPException(409, f"Invoice {number} from {supplier.name} is already recorded")
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == data.po_id).with_for_update().first()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    if po.supplier_id != supplier.id:
        raise HTTPException(400, f"Purchase order {po.po_number} belongs to another supplier")
    if po.status not in ("partially_received", "received"):
        raise HTTPException(400, f"Purchase order is {po.status}, invoice can be recorded only after goods are received")

    # amount checks
    if data.invoice_date > date.today():
        raise HTTPException(400, "Invoice date can't be in the future")
    if data.due_date < data.invoice_date:
        raise HTTPException(400, "Due date can't be before invoice date")
    if money(data.amount + data.tax_amount) != money(data.total_amount):
        raise HTTPException(400, f"Total should be amount + tax = {money(data.amount + data.tax_amount)}")
    can_bill = _received_value(db, po.id) - _invoiced_amount(db, po.id)
    if money(data.amount) > can_bill + TOLERANCE:
        raise HTTPException(400, f"Amount is more than received goods not yet invoiced (Rs {money(can_bill)})")

    inv = SupplierInvoice(invoice_number=number, supplier_id=supplier.id, po_id=po.id, invoice_date=data.invoice_date,
                          due_date=data.due_date, amount=money(data.amount), tax_amount=money(data.tax_amount),
                          total_amount=money(data.total_amount), paid_amount=0, payment_status="unpaid",
                          created_by=user.id, created_at=utcnow())
    db.add(inv)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"Invoice {number} is already recorded")
    audit(db, user, "supplier_invoice_recorded", "supplier_invoice", inv.id, None,
          {"po": po.id, "total": str(inv.total_amount)})
    db.commit()
    db.refresh(inv)
    return inv


@router.get("", response_model=List[InvoiceOut])
def list_invoices(payment_status: Optional[str] = None, supplier_id: Optional[int] = None,
                  po_id: Optional[int] = None, overdue: bool = False,
                  skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                  user: User = Depends(invoice_viewers), db: Session = Depends(get_db)):
    q = db.query(SupplierInvoice)
    if payment_status:
        q = q.filter(SupplierInvoice.payment_status == payment_status)
    if supplier_id:
        q = q.filter(SupplierInvoice.supplier_id == supplier_id)
    if po_id:
        q = q.filter(SupplierInvoice.po_id == po_id)
    if overdue:
        q = q.filter(SupplierInvoice.due_date < date.today(),
                     SupplierInvoice.payment_status.in_(("unpaid", "partially_paid")))
    return q.order_by(SupplierInvoice.id.desc()).offset(skip).limit(limit).all()


@router.get("/summary")
def invoice_summary(user: User = Depends(invoice_viewers), db: Session = Depends(get_db)):
    rows = (db.query(SupplierInvoice.payment_status, func.count(SupplierInvoice.id),
                     func.coalesce(func.sum(SupplierInvoice.total_amount), 0),
                     func.coalesce(func.sum(SupplierInvoice.paid_amount), 0))
            .group_by(SupplierInvoice.payment_status).all())
    by_status = {s: {"count": c, "total": money(t), "paid": money(p)} for s, c, t, p in rows}
    overdue = (db.query(func.count(SupplierInvoice.id))
               .filter(SupplierInvoice.due_date < date.today(),
                       SupplierInvoice.payment_status.in_(("unpaid", "partially_paid"))).scalar())
    outstanding = sum((v["total"] - v["paid"] for k, v in by_status.items() if k != "cancelled"), Decimal("0"))
    return {"by_status": by_status, "overdue_invoices": overdue, "outstanding_amount": money(outstanding)}


@router.get("/{invoice_id:int}", response_model=InvoiceOut)
def get_invoice(invoice_id: int, user: User = Depends(invoice_viewers), db: Session = Depends(get_db)):
    return get_or_404(db, SupplierInvoice, invoice_id, "Invoice")


@router.post("/{invoice_id:int}/payments", response_model=InvoiceOut)
def record_payment(invoice_id: int, data: InvoicePayment, user: User = Depends(finance),
                   db: Session = Depends(get_db)):
    inv = db.query(SupplierInvoice).filter(SupplierInvoice.id == invoice_id).with_for_update().first()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.payment_status in ("paid", "cancelled"):
        raise HTTPException(400, f"Invoice is already {inv.payment_status}")
    due = money(inv.total_amount) - money(inv.paid_amount)
    if money(data.amount) > due:
        raise HTTPException(400, f"Only Rs {due} is pending on this invoice")
    old = {"paid": str(money(inv.paid_amount)), "status": inv.payment_status}
    inv.paid_amount = money(inv.paid_amount) + money(data.amount)
    inv.payment_status = "paid" if money(inv.paid_amount) >= money(inv.total_amount) else "partially_paid"
    audit(db, user, "supplier_invoice_paid", "supplier_invoice", inv.id, old,
          {"paid": str(money(inv.paid_amount)), "status": inv.payment_status, "amount": str(money(data.amount)),
           "reference": data.reference})
    db.commit()
    db.refresh(inv)
    return inv


@router.post("/{invoice_id:int}/cancel", response_model=InvoiceOut)
def cancel_invoice(invoice_id: int, user: User = Depends(finance), db: Session = Depends(get_db)):
    """wrong bill entered - only if nothing is paid on it yet"""
    inv = db.query(SupplierInvoice).filter(SupplierInvoice.id == invoice_id).with_for_update().first()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if money(inv.paid_amount) > 0 or inv.payment_status != "unpaid":
        raise HTTPException(400, f"Invoice is {inv.payment_status}, it can't be cancelled")
    inv.payment_status = "cancelled"
    audit(db, user, "supplier_invoice_cancelled", "supplier_invoice", inv.id, {"status": "unpaid"},
          {"status": "cancelled"})
    db.commit()
    db.refresh(inv)
    return inv
