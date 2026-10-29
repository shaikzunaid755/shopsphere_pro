"""database level tests - services working on real tables"""
import pytest
from fastapi import HTTPException

import app.models as m
from app.services import inventory_service as inv
from tests.conftest import stocked_product


def test_add_stock_updates_quantity_and_writes_movement(db):
    product = stocked_product(db)
    row = db.query(m.Inventory).filter(m.Inventory.product_id == product.id, m.Inventory.variant_id.is_(None)).first()
    before_qty = row.quantity
    before_moves = db.query(m.StockMovement).count()
    inv.add_stock(db, row, 7, mtype="addition", ref_type="test", ref_id=1, note="pytest", user=None)
    db.commit()
    db.refresh(row)
    assert row.quantity == before_qty + 7
    assert db.query(m.StockMovement).count() == before_moves + 1


def test_cannot_reserve_more_than_available(db):
    product = stocked_product(db)
    free = inv.total_available(db, product.id)
    with pytest.raises(HTTPException) as e:
        inv.reserve(db, product.id, None, free + 1, "test", 1)
    assert e.value.status_code in (400, 409)
    db.rollback()
    assert inv.total_available(db, product.id) == free


def test_audit_logs_cannot_be_changed(client, db, h):
    # any admin action writes an audit row
    client.post("/notifications/send", json={"title": "Sale", "message": "Diwali sale", "roles": ["customer"]},
                headers=h("admin"))
    log = db.query(m.AuditLog).order_by(m.AuditLog.id.desc()).first()
    assert log is not None
    log.action = "changed"
    with pytest.raises((RuntimeError, ValueError)):
        db.commit()
    db.rollback()
    assert client.delete(f"/audit-logs/{log.id}", headers=h("admin")).status_code == 405


def test_supplier_po_receive_and_duplicate_invoice(client, db, h):
    admin = h("admin")
    s = client.post("/suppliers", json={"name": "Test Traders", "supplier_type": "supplier",
                                        "contact_person": "Ravi Kumar", "email": "test@traders.in",
                                        "phone": "9876500001", "address": "Shop 4, Ameerpet, Hyderabad"},
                    headers=admin)
    assert s.status_code == 201, s.text
    sid = s.json()["id"]
    assert client.post(f"/suppliers/{sid}/status", json={"action": "approve"}, headers=admin).status_code == 200
    product = stocked_product(db)
    assert client.post(f"/suppliers/{sid}/products", json={"product_id": product.id, "cost_price": 100},
                       headers=admin).status_code == 201
    wh = db.query(m.Warehouse).filter(m.Warehouse.status == "active").first()
    po = client.post("/purchase-orders", json={"supplier_id": sid, "warehouse_id": wh.id,
                                               "items": [{"product_id": product.id, "quantity": 10}]}, headers=admin)
    assert po.status_code == 201, po.text
    po_id, item_id = po.json()["id"], po.json()["items"][0]["id"]
    free = inv.total_available(db, product.id)
    client.post(f"/purchase-orders/{po_id}/submit", headers=admin)
    r = client.post(f"/purchase-orders/{po_id}/receive", json={"items": [{"item_id": item_id, "quantity": 10}]},
                    headers=admin)
    assert r.json()["status"] == "received"
    db.expire_all()
    assert inv.total_available(db, product.id) == free + 10
    bill = {"invoice_number": "TT/001", "supplier_id": sid, "po_id": po_id, "invoice_date": "2026-10-29",
            "due_date": "2026-11-28", "amount": 1000, "tax_amount": 180, "total_amount": 1180}
    assert client.post("/supplier-invoices", json=bill, headers=h("finance")).status_code == 201
    assert client.post("/supplier-invoices", json=bill, headers=h("finance")).status_code == 409
