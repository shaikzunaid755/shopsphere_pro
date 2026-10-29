"""end to end: cart -> order -> payment -> cancellation -> refund, plus duplicate request safety"""
from decimal import Decimal

import app.models as m
from tests.conftest import place_order, stocked_product


def _free_stock(db, product_id):
    db.expire_all()
    rows = db.query(m.Inventory).filter(m.Inventory.product_id == product_id, m.Inventory.variant_id.is_(None)).all()
    return sum(r.quantity - r.reserved for r in rows)


def test_cart_add_update_remove(client, db, h):
    product = stocked_product(db)
    client.delete("/cart", headers=h("anjali"))
    r = client.post("/cart/items", json={"product_id": product.id, "quantity": 1}, headers=h("anjali"))
    assert r.status_code in (200, 201), r.text
    cart = client.get("/cart", headers=h("anjali")).json()
    assert cart["item_count"] >= 1
    item_id = cart["items"][0]["item_id"]
    assert client.put(f"/cart/items/{item_id}", json={"quantity": 2}, headers=h("anjali")).status_code == 200
    assert client.put(f"/cart/items/{item_id}", json={"quantity": 0}, headers=h("anjali")).status_code == 422
    assert client.delete(f"/cart/items/{item_id}", headers=h("anjali")).status_code in (200, 204)


def test_same_idempotency_key_gives_same_order(client, db, h):
    first = place_order(client, db, h("rahul"), "rahul@example.com", "dup-order-key")
    again = place_order(client, db, h("rahul"), "rahul@example.com", "dup-order-key")
    assert (first.get("id") or first.get("order_id")) == (again.get("id") or again.get("order_id"))


def test_full_online_order_payment_cancel_refund(client, db, h):
    rahul = h("rahul")
    product = stocked_product(db)
    stock_before = _free_stock(db, product.id)

    # 1. cart -> order (stock is held for the order)
    order = place_order(client, db, rahul, "rahul@example.com", "flow-order-1")
    oid = order.get("id") or order.get("order_id")
    assert _free_stock(db, product.id) == stock_before - 1

    # 2. payment - same Idempotency-Key twice must not create two payments
    r1 = client.post("/payments", json={"order_id": oid, "method": "card"}, headers={**rahul, "Idempotency-Key": "pay-1"})
    assert r1.status_code in (200, 201), r1.text
    r2 = client.post("/payments", json={"order_id": oid, "method": "card"}, headers={**rahul, "Idempotency-Key": "pay-1"})
    assert r2.json()["payment_id"] == r1.json()["payment_id"]
    pid = r1.json()["payment_id"]
    r = client.post(f"/payments/{pid}/sandbox-confirm", json={"result": "success"}, headers=rahul)
    assert r.status_code == 200, r.text
    payment = client.get(f"/payments/{pid}", headers=rahul).json()
    assert payment["status"] == "succeeded"
    assert len(client.get(f"/payments/orders/{oid}", headers=h("finance")).json()) >= 1

    # 3. cancellation by the customer
    r = client.post(f"/cancellations/orders/{oid}", json={"reason": "Ordered by mistake"}, headers=rahul)
    assert r.status_code in (200, 201), r.text
    db.expire_all()
    assert db.get(m.Order, oid).status == "cancelled"
    assert _free_stock(db, product.id) == stock_before      # held stock released

    # 4. refund - created by the cancellation (paid order) or by finance, then processed
    refunds = client.get("/refunds", params={"order_id": oid}, headers=h("finance")).json()
    if not refunds:
        r = client.post("/refunds", json={"order_id": oid, "reason": "Order cancelled"}, headers=h("finance"))
        assert r.status_code in (200, 201), r.text
        refunds = [r.json()]
    refund = refunds[0]
    if refund["status"] != "processed":
        assert client.post(f"/refunds/{refund['id']}/process", headers=h("rahul")).status_code == 403
        r = client.post(f"/refunds/{refund['id']}/process", headers=h("finance"))
        assert r.status_code == 200, r.text
        refund = r.json()
    assert refund["status"] == "processed"
    assert Decimal(refund["amount"]) == Decimal(str(payment["amount"]))
    # processing again must not pay the customer twice
    assert client.post(f"/refunds/{refund['id']}/process", headers=h("finance")).status_code in (400, 409)


def test_failed_payment_keeps_order_unpaid(client, db, h):
    rahul = h("rahul")
    order = place_order(client, db, rahul, "rahul@example.com", "flow-fail-1")
    oid = order.get("id") or order.get("order_id")
    pid = client.post("/payments", json={"order_id": oid, "method": "card"}, headers=rahul).json()["payment_id"]
    r = client.post(f"/payments/{pid}/sandbox-confirm", json={"result": "fail", "failure_reason": "card declined"},
                    headers=rahul)
    assert r.status_code == 200, r.text
    assert client.get(f"/payments/{pid}", headers=rahul).json()["status"] == "failed"
    # nothing was paid, so finance can't refund it
    r = client.post("/refunds", json={"order_id": oid, "reason": "test"}, headers=h("finance"))
    assert r.status_code in (400, 409)


def test_webhook_with_bad_signature_is_rejected(client):
    r = client.post("/payments/webhook", content=b'{"type": "payment_intent.succeeded"}',
                    headers={"Stripe-Signature": "t=1,v1=fake", "Content-Type": "application/json"})
    assert r.status_code in (400, 401)
