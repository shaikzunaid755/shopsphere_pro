SUPER_ADMIN = "super_admin"
ADMIN = "admin"
PRODUCT_MANAGER = "product_manager"
INVENTORY_MANAGER = "inventory_manager"
ORDER_MANAGER = "order_manager"
SUPPORT_AGENT = "support_agent"
CUSTOMER = "customer"
DELIVERY_MANAGER = "delivery_manager"
FINANCE_MANAGER = "finance_manager"

ALL_ROLES = [SUPER_ADMIN, ADMIN, PRODUCT_MANAGER, INVENTORY_MANAGER, ORDER_MANAGER,
             SUPPORT_AGENT, CUSTOMER, DELIVERY_MANAGER, FINANCE_MANAGER]

STAFF_ROLES = [r for r in ALL_ROLES if r != CUSTOMER]

ROLE_DESCRIPTIONS = {
    SUPER_ADMIN: "Full system configuration and access",
    ADMIN: "Manage products, users, orders and platform operations",
    PRODUCT_MANAGER: "Manage product catalogs, categories and pricing",
    INVENTORY_MANAGER: "Manage stock, warehouses and inventory movements",
    ORDER_MANAGER: "Process orders, cancellations and fulfillment",
    SUPPORT_AGENT: "Handle customer complaints and support requests",
    CUSTOMER: "Browse products, place orders, make payments and manage returns",
    DELIVERY_MANAGER: "Manage shipments, delivery assignments and tracking",
    FINANCE_MANAGER: "Review payments, refunds, settlements and financial reports",
}

# permission codes, stored in db and linked to roles by the seed script
DEFAULT_PERMISSIONS = {
    "users.manage": [ADMIN],
    "catalog.manage": [ADMIN, PRODUCT_MANAGER],
    "pricing.manage": [ADMIN, PRODUCT_MANAGER],
    "inventory.manage": [ADMIN, INVENTORY_MANAGER],
    "orders.manage": [ADMIN, ORDER_MANAGER],
    "shipping.manage": [ADMIN, DELIVERY_MANAGER],
    "payments.view": [ADMIN, FINANCE_MANAGER],
    "refunds.manage": [ADMIN, FINANCE_MANAGER],
    "support.manage": [ADMIN, SUPPORT_AGENT],
    "reports.view": [ADMIN, FINANCE_MANAGER],
    "audit.view": [ADMIN],
    "shop.use": [CUSTOMER],
}