from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.utils import utcnow


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    supplier_type = Column(String(20), default="supplier")  # supplier / seller
    contact_person = Column(String(120))
    email = Column(String(150), unique=True, nullable=False)
    phone = Column(String(15))
    gst_number = Column(String(20))
    address = Column(String(255))
    # pending, approved, rejected, suspended
    status = Column(String(20), default="pending")
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class SupplierHistory(Base):
    __tablename__ = "supplier_history"

    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id", ondelete="CASCADE"))
    action = Column(String(40))
    details = Column(Text)
    changed_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)


class SupplierProduct(Base):
    __tablename__ = "supplier_products"
    __table_args__ = (UniqueConstraint("supplier_id", "product_id", name="uq_supplier_product"),)

    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.id"))
    supplier_sku = Column(String(60))
    cost_price = Column(Numeric(12, 2))


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True)
    po_number = Column(String(40), unique=True, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    # draft, sent, partially_received, received, cancelled
    status = Column(String(30), default="draft")
    expected_date = Column(DateTime)
    total_amount = Column(Numeric(12, 2), default=0)
    note = Column(String(255))
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    items = relationship("PurchaseOrderItem", back_populates="po", cascade="all, delete-orphan")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id = Column(Integer, primary_key=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    ordered_qty = Column(Integer, nullable=False)
    received_qty = Column(Integer, default=0)
    unit_cost = Column(Numeric(12, 2), nullable=False)

    po = relationship("PurchaseOrder", back_populates="items")


class SupplierInvoice(Base):
    __tablename__ = "supplier_invoices"
    __table_args__ = (UniqueConstraint("supplier_id", "invoice_number", name="uq_supplier_invoice"),)

    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(60), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    invoice_date = Column(DateTime, nullable=False)
    due_date = Column(DateTime)
    amount = Column(Numeric(12, 2), nullable=False)
    tax_amount = Column(Numeric(12, 2), default=0)
    total_amount = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(12, 2), default=0)
    payment_status = Column(String(20), default="unpaid")  # unpaid / partially_paid / paid
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)