"""authentication, role permissions and customer data isolation"""
from tests.conftest import login, place_order


def test_login_and_me(client, h):
    r = client.get("/auth/me", headers=h("rahul"))
    assert r.status_code == 200
    assert r.json()["email"] == "rahul@example.com"


def test_wrong_password_is_rejected(client):
    r = client.post("/auth/login", json={"email": "rahul@example.com", "password": "Wrong@123"})
    assert r.status_code in (400, 401)


def test_no_token_and_bad_token(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"}).status_code == 401


def test_staff_can_log_in(client):
    assert login(client, "admin@shopsphere.com")
    assert login(client, "finance@shopsphere.com")


def test_customer_blocked_from_staff_endpoints(client, h):
    for path in ("/payments", "/refunds", "/audit-logs", "/dashboard", "/reports/sales", "/suppliers"):
        assert client.get(path, headers=h("rahul")).status_code == 403, path


def test_staff_only_see_their_area(client, h):
    assert client.get("/dashboard/revenue", headers=h("orders")).status_code == 403
    assert client.get("/dashboard/revenue", headers=h("finance")).status_code == 200
    assert client.get("/reports/payments", headers=h("inventory")).status_code == 403
    assert client.get("/audit-logs", headers=h("finance")).status_code == 403
    assert client.get("/audit-logs", headers=h("admin")).status_code == 200


def test_customer_cannot_see_other_customers_order(client, db, h):
    order = place_order(client, db, h("rahul"), "rahul@example.com", "isolation-test-1")
    oid = order.get("id") or order.get("order_id")
    assert client.get(f"/orders/{oid}", headers=h("rahul")).status_code == 200
    # anjali gets "not found", she can't even tell the order exists
    assert client.get(f"/orders/{oid}", headers=h("anjali")).status_code in (403, 404)
    assert client.post(f"/cancellations/orders/{oid}", json={"reason": "not mine"},
                       headers=h("anjali")).status_code in (403, 404)
    # staff can see it
    assert client.get(f"/orders/{oid}", headers=h("orders")).status_code == 200


def test_customer_cannot_change_order_status(client, db, h):
    order = place_order(client, db, h("rahul"), "rahul@example.com", "isolation-test-2")
    oid = order.get("id") or order.get("order_id")
    r = client.patch(f"/orders/{oid}/status", json={"status": "shipped"}, headers=h("rahul"))
    assert r.status_code in (403, 422)
