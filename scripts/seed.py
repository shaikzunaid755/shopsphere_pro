"""
Sample data for testing.

run from project root:
    python -m scripts.seed

Safe to run again - it skips anything that already exists.
All seeded users have password:  Password@123
"""
from datetime import timedelta
from decimal import Decimal

from app.core.database import SessionLocal
from app.core.roles import ALL_ROLES, ROLE_DESCRIPTIONS, DEFAULT_PERMISSIONS
from app.core.security import hash_password
from app.core.utils import utcnow
from app.models import (Role, Permission, RolePermission, User, Cart, Address, Category, Brand, Product,
                        ProductVariant, Warehouse, Inventory, StockMovement, TaxRule, ShippingMethod, Coupon,
                        Promotion, CODRule, ShippingProvider, MessageProvider, Supplier, Attribute,
                        ProductAttributeValue)

PASSWORD = "Password@123"

STAFF = [
    ("Super Admin", "superadmin@shopsphere.com", "9000000001", "super_admin"),
    ("Admin User", "admin@shopsphere.com", "9000000002", "admin"),
    ("Priya Product", "product@shopsphere.com", "9000000003", "product_manager"),
    ("Ravi Inventory", "inventory@shopsphere.com", "9000000004", "inventory_manager"),
    ("Kiran Orders", "orders@shopsphere.com", "9000000005", "order_manager"),
    ("Sana Support", "support@shopsphere.com", "9000000006", "support_agent"),
    ("Arjun Delivery", "delivery@shopsphere.com", "9000000007", "delivery_manager"),
    ("Meena Finance", "finance@shopsphere.com", "9000000008", "finance_manager"),
]

CUSTOMERS = [
    ("Rahul Sharma", "rahul@example.com", "9876543210"),
    ("Anjali Reddy", "anjali@example.com", "9876543211"),
]


def get_or_create(db, model, defaults=None, **filters):
    obj = db.query(model).filter_by(**filters).first()
    if obj:
        return obj, False
    obj = model(**filters, **(defaults or {}))
    db.add(obj)
    db.flush()
    return obj, True


def seed(db):
    # roles + permissions
    roles = {}
    for name in ALL_ROLES:
        roles[name], _ = get_or_create(db, Role, {"description": ROLE_DESCRIPTIONS[name]}, name=name)
    for code, role_names in DEFAULT_PERMISSIONS.items():
        perm, _ = get_or_create(db, Permission, {"description": code.replace(".", " ")}, code=code)
        for rn in role_names:
            get_or_create(db, RolePermission, role_id=roles[rn].id, permission_id=perm.id)

    pw = hash_password(PASSWORD)
    for name, email, phone, role in STAFF:
        get_or_create(db, User, {"full_name": name, "phone": phone, "password_hash": pw, "role_id": roles[role].id,
                                 "is_email_verified": True, "registration_status": "verified"}, email=email)

    customers = []
    for name, email, phone in CUSTOMERS:
        u, created = get_or_create(db, User, {"full_name": name, "phone": phone, "password_hash": pw,
                                              "role_id": roles["customer"].id, "is_email_verified": True,
                                              "registration_status": "verified"}, email=email)
        customers.append(u)
        if created:
            db.add(Cart(user_id=u.id))
            db.add(Address(user_id=u.id, full_name=name, phone=phone, line1="Plot 12, Road No 3",
                           line2="Near Metro Station", city="Hyderabad", state="Telangana", postal_code="500047",
                           address_type="shipping", is_default=True))
            db.add(Address(user_id=u.id, full_name=name, phone=phone, line1="Flat 402, Sai Residency",
                           city="Hyderabad", state="Telangana", postal_code="500016",
                           address_type="billing", is_default=True))

    # catalog
    electronics, _ = get_or_create(db, Category, {"name": "Electronics", "description": "Gadgets"}, slug="electronics")
    mobiles, _ = get_or_create(db, Category, {"name": "Mobiles", "parent_id": electronics.id}, slug="mobiles")
    clothing, _ = get_or_create(db, Category, {"name": "Clothing"}, slug="clothing")
    tshirts, _ = get_or_create(db, Category, {"name": "T-Shirts", "parent_id": clothing.id}, slug="t-shirts")

    samsung, _ = get_or_create(db, Brand, {"description": "Samsung Electronics"}, name="Samsung")
    urban, _ = get_or_create(db, Brand, {"description": "Casual wear"}, name="UrbanThreads")

    supplier, _ = get_or_create(db, Supplier, {"name": "Deccan Distributors", "contact_person": "Mahesh",
                                               "phone": "9988776655", "gst_number": "36ABCDE1234F1Z5",
                                               "address": "Begumpet, Hyderabad", "status": "approved",
                                               "approved_at": utcnow()}, email="sales@deccandist.com")

    phone, _ = get_or_create(db, Product, {
        "name": "Samsung Galaxy M15", "slug": "samsung-galaxy-m15", "category_id": mobiles.id,
        "brand_id": samsung.id, "supplier_id": supplier.id, "base_price": Decimal("12999.00"),
        "description": "6000 mAh battery, 6.5 inch display", "is_cod_allowed": True}, sku="SAM-M15")
    earbuds, _ = get_or_create(db, Product, {
        "name": "Samsung Buds FE", "slug": "samsung-buds-fe", "category_id": electronics.id,
        "brand_id": samsung.id, "base_price": Decimal("4999.00"), "sale_price": Decimal("3999.00"),
        "description": "Wireless earbuds with ANC"}, sku="SAM-BUDSFE")
    tshirt, _ = get_or_create(db, Product, {
        "name": "Cotton Crew T-Shirt", "slug": "cotton-crew-t-shirt", "category_id": tshirts.id,
        "brand_id": urban.id, "base_price": Decimal("599.00"), "description": "100% cotton regular fit"},
        sku="UT-CREW")

    v_red_m, _ = get_or_create(db, ProductVariant, {"product_id": tshirt.id, "name": "Red / M", "color": "Red",
                                                    "size": "M"}, sku="UT-CREW-RED-M")
    v_blk_l, _ = get_or_create(db, ProductVariant, {"product_id": tshirt.id, "name": "Black / L", "color": "Black",
                                                    "size": "L", "price": Decimal("649.00")}, sku="UT-CREW-BLK-L")

    material, _ = get_or_create(db, Attribute, {"data_type": "choice", "allowed_values": "Cotton,Polyester,Linen",
                                                "category_id": clothing.id}, name="Material")
    get_or_create(db, ProductAttributeValue, {"value": "Cotton"}, product_id=tshirt.id, attribute_id=material.id)

    # warehouses + stock
    hyd, _ = get_or_create(db, Warehouse, {"name": "Hyderabad Main", "address": "Medchal", "city": "Hyderabad",
                                           "capacity": 10000}, code="HYD01")
    blr, _ = get_or_create(db, Warehouse, {"name": "Bengaluru Hub", "address": "Hosur Road", "city": "Bengaluru",
                                           "capacity": 5000}, code="BLR01")
    stock = [(phone, None, hyd, 50, 5), (earbuds, None, hyd, 80, 10), (tshirt, v_red_m, hyd, 100, 15),
             (tshirt, v_blk_l, hyd, 60, 10), (phone, None, blr, 20, 5)]
    for product, variant, wh, qty, threshold in stock:
        row, created = get_or_create(db, Inventory, {"quantity": qty, "reserved": 0, "min_threshold": threshold},
                                     product_id=product.id, variant_id=variant.id if variant else None,
                                     warehouse_id=wh.id)
        if created:
            db.add(StockMovement(inventory_id=row.id, product_id=product.id,





                                 variant_id=variant.id if variant else None, warehouse_id=wh.id,
                                 movement_type="addition", quantity=qty, note="opening stock"))

    # tax: default 18% GST, clothing 5%
    get_or_create(db, TaxRule, {"rate": Decimal("18.00")}, name="GST 18%", product_id=None, category_id=None)
    get_or_create(db, TaxRule, {"rate": Decimal("5.00")}, name="GST 5% Apparel", category_id=clothing.id)

    get_or_create(db, ShippingMethod, {"name": "Standard Delivery", "base_charge": Decimal("49"),
                                       "per_item_charge": Decimal("10"), "free_above": Decimal("999"),
                                       "estimated_days": 5, "cod_supported": True}, code="STANDARD")
    get_or_create(db, ShippingMethod, {"name": "Express Delivery", "base_charge": Decimal("149"),
                                       "per_item_charge": Decimal("20"), "estimated_days": 2,
                                       "cod_supported": False, "allowed_states": "Telangana,Karnataka"},
                  code="EXPRESS")

    now = utcnow()
    get_or_create(db, Coupon, {"description": "10% off up to 500", "discount_type": "percentage",
                               "discount_value": Decimal("10"), "max_discount": Decimal("500"),
                               "min_purchase": Decimal("500"), "usage_limit": 1000, "per_user_limit": 1,
                               "valid_from": now - timedelta(days=1), "valid_to": now + timedelta(days=90)},
                  code="WELCOME10")
    get_or_create(db, Coupon, {"description": "Flat 200 off", "discount_type": "fixed",
                               "discount_value": Decimal("200"), "min_purchase": Decimal("1500"),
                               "usage_limit": 500, "per_user_limit": 2,
                               "valid_from": now - timedelta(days=1), "valid_to": now + timedelta(days=30)},
                  code="FLAT200")
    get_or_create(db, Promotion, {"description": "Clothing festive sale", "promo_type": "category",
                                  "category_id": clothing.id, "discount_type": "percentage",
                                  "discount_value": Decimal("10"), "min_quantity": 2,
                                  "start_date": now - timedelta(days=1), "end_date": now + timedelta(days=30)},
                  name="Buy 2 Tees Get 10% Off")

    if not db.query(CODRule).first():
        db.add(CODRule(is_enabled=True, max_order_amount=Decimal("20000"), min_order_amount=Decimal("0"),
                       blocked_postal_codes="110001"))

    get_or_create(db, ShippingProvider, {"name": "BlueDart", "contact_phone": "1860233",
                                         "tracking_url": "https://www.bluedart.com/tracking"}, code="BLUEDART")
    get_or_create(db, ShippingProvider, {"name": "Delhivery", "contact_phone": "1244002",
                                         "tracking_url": "https://www.delhivery.com/track"}, code="DELHIVERY")
    get_or_create(db, MessageProvider, {"name": "Console email", "provider_type": "console"}, channel="email")
    get_or_create(db, MessageProvider, {"name": "Console SMS", "provider_type": "console"}, channel="sms")

    db.commit()
    return {"customers": [c.email for c in customers]}


if __name__ == "__main__":
    session = SessionLocal()
    try:
        seed(session)
        print("Sample data loaded. Login with any seeded email and password:", PASSWORD)
        for _, email, _, role in STAFF:
            print(f"  {role:18} {email}")
        for _, email, _ in CUSTOMERS:
            print(f"  {'customer':18} {email}")
    finally:
        session.close()