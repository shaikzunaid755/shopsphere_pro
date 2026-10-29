"""
Test setup - runs the real app against a separate sqlite database (test_shopsphere.db),
so the MySQL data is never touched. Tables are created fresh and the normal seed data is loaded.
"""
import os
import tempfile

# must be set before the app is imported
os.environ["DATABASE_URL"] = "sqlite:///./test_shopsphere.db"
os.environ["STRIPE_SECRET_KEY"] = ""          # always the sandbox gateway in tests
os.environ["SMTP_HOST"] = ""                  # emails only logged
os.environ["EXPORT_DIR"] = tempfile.mkdtemp(prefix="shopsphere_exports_")

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, SessionLocal, engine
from app.main import app
import app.models as m
from scripts.seed import seed

PASSWORD = "Password@123"


@pytest.fixture(scope="session", autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    seed(db)
    db.commit()
    db.close()
    yield
    engine.dispose()


@pytest.fixture(scope="session")
def client(database):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


_tokens = {}


def login(client, email, password=PASSWORD):
    """customers use /auth/login, staff use /auth/staff/login"""
    for path in ("/auth/login", "/auth/staff/login"):
        r = client.post(path, json={"email": email, "password": password})
        if r.status_code == 200:
            return r.json()["access_token"]
    raise AssertionError(f"login failed for {email}: {r.status_code} {r.text}")


def auth(client, email):
    if email not in _tokens:
        _tokens[email] = login(client, email)
    return {"Authorization": f"Bearer {_tokens[email]}"}


@pytest.fixture(scope="session")
def h(client):
    """h('rahul') -> auth header for that seeded user"""
    emails = {"rahul": "rahul@example.com", "anjali": "anjali@example.com", "admin": "admin@shopsphere.com",
              "super": "superadmin@shopsphere.com", "finance": "finance@shopsphere.com",
              "orders": "orders@shopsphere.com", "inventory": "inventory@shopsphere.com",
              "support": "support@shopsphere.com", "product": "product@shopsphere.com",
              "delivery": "delivery@shopsphere.com"}
    return lambda name: auth(client, emails[name])


def user_id(db, email):
    return db.query(m.User).filter(m.User.email == email).first().id


def object_id(data):
    """place-order / payment responses use slightly different shapes"""
    for key in ("id", "order_id", "payment_id"):
        if isinstance(data, dict) and key in data:
            return data[key]
    if isinstance(data, dict) and isinstance(data.get("order"), dict):
        return data["order"]["id"]
    raise AssertionError(f"no id in response: {data}")


def address_for(db, uid):
    addr = (db.query(m.Address).filter(m.Address.user_id == uid, m.Address.is_deleted == False)  # noqa: E712
            .order_by(m.Address.id).first())
    if not addr:
        addr = m.Address(user_id=uid, full_name="Test Customer", phone="9876543210", line1="Plot 12, Road No 3",
                         city="Hyderabad", state="Telangana", postal_code="500047", country="India",
                         address_type="home", is_default=True)
        db.add(addr)
        db.commit()
    return addr.id


def stocked_product(db, need=5):
    """an active product (no variant) with enough free stock"""
    rows = (db.query(m.Inventory, m.Product).join(m.Product, m.Product.id == m.Inventory.product_id)
            .filter(m.Product.status == "active", m.Inventory.variant_id.is_(None)).all())
    for inv, product in rows:
        if inv.quantity - inv.reserved >= need:
            return product
    raise AssertionError("seed data has no product with free stock")


def place_order(client, db, header, email, key, quantity=1, payment_method="online"):
    """cart -> checkout, returns the order json"""
    uid = user_id(db, email)
    product = stocked_product(db)
    client.delete("/cart", headers=header)
    r = client.post("/cart/items", json={"product_id": product.id, "quantity": quantity}, headers=header)
    assert r.status_code in (200, 201), r.text
    addr = address_for(db, uid)
    methods = db.query(m.ShippingMethod).filter(m.ShippingMethod.is_active == True).all()  # noqa: E712
    last = None
    for sm in methods:
        body = {"shipping_address_id": addr, "shipping_method_id": sm.id, "payment_method": payment_method}
        last = client.post("/checkout/validate", json=body, headers=header)
        if last.status_code == 200 and not last.json().get("errors"):
            r = client.post("/checkout/place-order", json=body, headers={**header, "Idempotency-Key": key})
            assert r.status_code in (200, 201), r.text
            return r.json()
    raise AssertionError(f"no shipping method accepted the order: {last.text if last else 'none'}")
