# Module 43: Seller and Supplier Management
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import require_roles, get_or_404
from app.core.roles import ADMIN
from app.core.utils import from_json, money, to_json, utcnow
from app.models import Product, Supplier, SupplierHistory, SupplierProduct, User
from app.schemas.procurement import (SupplierCreate, SupplierUpdate, SupplierDecision, SupplierProductLink,
                                     SupplierOut)

router = APIRouter(prefix="/suppliers", tags=["43 Seller and Supplier Management"])

admin_only = require_roles(ADMIN)
supplier_staff = require_roles(ADMIN, "inventory_manager", "product_manager")

# pending -> approved / rejected, approved <-> suspended
MOVES = {
    "approve": (("pending", "rejected"), "approved"),
    "reject": (("pending",), "rejected"),
    "suspend": (("approved",), "suspended"),
    "reactivate": (("suspended",), "approved"),
}


def _history(db, supplier_id, action, details, user):
    db.add(SupplierHistory(supplier_id=supplier_id, action=action, details=to_json(details),
                           changed_by=user.id, created_at=utcnow()))


def _check_unique(db, email=None, gst=None, skip_id=None):
    q = db.query(Supplier)
    if skip_id:
        q = q.filter(Supplier.id != skip_id)
    if email and q.filter(Supplier.email == email).first():
        raise HTTPException(409, "Another supplier already uses this email")
    if gst and q.filter(Supplier.gst_number == gst).first():
        raise HTTPException(409, "Another supplier already has this GST number")


@router.post("", response_model=SupplierOut, status_code=201)
def register_supplier(data: SupplierCreate, user: User = Depends(supplier_staff), db: Session = Depends(get_db)):
    email = data.email.lower()
    _check_unique(db, email, data.gst_number)
    s = Supplier(name=data.name.strip(), supplier_type=data.supplier_type, contact_person=data.contact_person.strip(),
                 email=email, phone=data.phone, gst_number=data.gst_number, address=data.address.strip(),
                 status="pending", created_at=utcnow(), updated_at=utcnow())
    db.add(s)
    db.flush()
    _history(db, s.id, "registered", {"type": s.supplier_type, "status": "pending"}, user)
    audit(db, user, "supplier_registered", "supplier", s.id, None, {"name": s.name})
    db.commit()
    db.refresh(s)
    return s


@router.get("", response_model=List[SupplierOut])
def list_suppliers(status: Optional[str] = None, supplier_type: Optional[str] = None, search: Optional[str] = None,
                   skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                   user: User = Depends(supplier_staff), db: Session = Depends(get_db)):
    q = db.query(Supplier)
    if status:
        q = q.filter(Supplier.status == status)
    if supplier_type:
        q = q.filter(Supplier.supplier_type == supplier_type)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(or_(Supplier.name.like(like), Supplier.email.like(like), Supplier.gst_number.like(like)))
    return q.order_by(Supplier.id.desc()).offset(skip).limit(limit).all()


@router.get("/{supplier_id:int}")
def get_supplier(supplier_id: int, user: User = Depends(supplier_staff), db: Session = Depends(get_db)):
    s = get_or_404(db, Supplier, supplier_id, "Supplier")
    rows = (db.query(SupplierProduct, Product.name).join(Product, Product.id == SupplierProduct.product_id)
            .filter(SupplierProduct.supplier_id == s.id).all())
    products = [{"product_id": sp.product_id, "product_name": name, "supplier_sku": sp.supplier_sku,
                 "cost_price": money(sp.cost_price)} for sp, name in rows]
    return {**SupplierOut.model_validate(s).model_dump(), "products": products}


@router.patch("/{supplier_id:int}", response_model=SupplierOut)
def update_supplier(supplier_id: int, data: SupplierUpdate, user: User = Depends(supplier_staff),
                    db: Session = Depends(get_db)):
    s = get_or_404(db, Supplier, supplier_id, "Supplier")
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if "email" in changes:
        changes["email"] = changes["email"].lower()
    _check_unique(db, changes.get("email"), changes.get("gst_number"), skip_id=s.id)
    old = {k: getattr(s, k) for k in changes if getattr(s, k) != changes[k]}
    if not old:
        raise HTTPException(400, "Nothing changed")
    for key in old:
        setattr(s, key, changes[key])
    s.updated_at = utcnow()
    _history(db, s.id, "updated", {"old": old, "new": {k: changes[k] for k in old}}, user)
    audit(db, user, "supplier_updated", "supplier", s.id, old, {k: changes[k] for k in old})
    db.commit()
    db.refresh(s)
    return s


@router.post("/{supplier_id:int}/status", response_model=SupplierOut)
def change_status(supplier_id: int, data: SupplierDecision, user: User = Depends(admin_only),
                  db: Session = Depends(get_db)):
    """approval workflow: approve / reject / suspend / reactivate"""
    action = data.action
    s = db.query(Supplier).filter(Supplier.id == supplier_id).with_for_update().first()
    if not s:
        raise HTTPException(404, "Supplier not found")
    allowed, new_status = MOVES[action]
    if s.status not in allowed:
        raise HTTPException(400, f"Supplier is {s.status}, can't {action}")
    if action in ("reject", "suspend") and not (data.note and len(data.note.strip()) >= 5):
        raise HTTPException(400, f"Please give a reason to {action} (min 5 characters)")
    old = s.status
    s.status = new_status
    s.updated_at = utcnow()
    if new_status == "approved":
        s.approved_by = user.id
        s.approved_at = utcnow()
    _history(db, s.id, action, {"from": old, "to": new_status, "note": data.note}, user)
    audit(db, user, f"supplier_{action}", "supplier", s.id, {"status": old}, {"status": new_status})
    db.commit()
    db.refresh(s)
    return s


# ---------------- products of a supplier ----------------

@router.post("/{supplier_id:int}/products", status_code=201)
def link_product(supplier_id: int, data: SupplierProductLink, user: User = Depends(supplier_staff),
                 db: Session = Depends(get_db)):
    s = get_or_404(db, Supplier, supplier_id, "Supplier")
    if s.status != "approved":
        raise HTTPException(400, f"Supplier is {s.status}, only approved suppliers can supply products")
    product = get_or_404(db, Product, data.product_id, "Product")
    if db.query(SupplierProduct).filter(SupplierProduct.supplier_id == s.id,
                                        SupplierProduct.product_id == product.id).first():
        raise HTTPException(409, "This product is already linked to the supplier")
    db.add(SupplierProduct(supplier_id=s.id, product_id=product.id, supplier_sku=data.supplier_sku,
                           cost_price=data.cost_price))
    if data.set_primary:
        product.supplier_id = s.id
    _history(db, s.id, "product_linked", {"product_id": product.id, "cost_price": str(data.cost_price),
                                          "primary": data.set_primary}, user)
    audit(db, user, "supplier_product_linked", "supplier", s.id, None, {"product": product.id})
    db.commit()
    return {"supplier_id": s.id, "product_id": product.id, "product_name": product.name,
            "cost_price": money(data.cost_price), "primary_supplier": product.supplier_id == s.id}


@router.delete("/{supplier_id:int}/products/{product_id:int}", status_code=204)
def unlink_product(supplier_id: int, product_id: int, user: User = Depends(supplier_staff),
                   db: Session = Depends(get_db)):
    link = (db.query(SupplierProduct)
            .filter(SupplierProduct.supplier_id == supplier_id, SupplierProduct.product_id == product_id).first())
    if not link:
        raise HTTPException(404, "Product is not linked to this supplier")
    product = db.get(Product, product_id)
    if product and product.supplier_id == supplier_id:
        product.supplier_id = None
    db.delete(link)
    _history(db, supplier_id, "product_unlinked", {"product_id": product_id}, user)
    db.commit()


@router.get("/{supplier_id:int}/history")
def supplier_history(supplier_id: int, user: User = Depends(supplier_staff), db: Session = Depends(get_db)):
    s = get_or_404(db, Supplier, supplier_id, "Supplier")
    rows = (db.query(SupplierHistory).filter(SupplierHistory.supplier_id == s.id)
            .order_by(SupplierHistory.id).all())
    return {"supplier": s.name, "status": s.status,
            "history": [{"action": h.action, "details": from_json(h.details) or h.details, "by": h.changed_by, "at": h.created_at}
                        for h in rows]}
