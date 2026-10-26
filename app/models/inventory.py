from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.utils import utcnow


class Warehouse(Base):
    __tablename__ = "warehouses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(20), unique=True, nullable=False)
    address = Column(String(255))
    city = Column(String(80))
    capacity = Column(Integer, default=0)  # max units it can hold
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=utcnow)


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (UniqueConstraint("product_id", "variant_id", "warehouse_id", name="uq_stock_row"),)

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    quantity = Column(Integer, default=0, nullable=False)  # physically on hand
    reserved = Column(Integer, default=0, nullable=False)  # held for pending orders
    min_threshold = Column(Integer, default=0)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    product = relationship("Product")
    variant = relationship("ProductVariant")
    warehouse = relationship("Warehouse")

    @property
    def available(self):
        return self.quantity - self.reserved


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True)
    inventory_id = Column(Integer, ForeignKey("inventory.id"), index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    # addition, reduction, reserve, release, sale, transfer_out, transfer_in, return, po_receipt
    movement_type = Column(String(30), nullable=False)
    quantity = Column(Integer, nullable=False)
    reference_type = Column(String(30))
    reference_id = Column(Integer)
    note = Column(String(255))
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class InventoryTransfer(Base):
    __tablename__ = "inventory_transfers"

    id = Column(Integer, primary_key=True)
    transfer_number = Column(String(40), unique=True, nullable=False)
    from_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    to_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    status = Column(String(20), default="requested")  # requested/dispatched/received/cancelled
    note = Column(String(255))
    requested_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)
    dispatched_at = Column(DateTime)
    received_at = Column(DateTime)

    items = relationship("TransferItem", back_populates="transfer", cascade="all, delete-orphan")


class TransferItem(Base):
    __tablename__ = "transfer_items"

    id = Column(Integer, primary_key=True)
    transfer_id = Column(Integer, ForeignKey("inventory_transfers.id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    requested_qty = Column(Integer, nullable=False)
    dispatched_qty = Column(Integer, default=0)
    received_qty = Column(Integer, default=0)

    transfer = relationship("InventoryTransfer", back_populates="items")


class ThresholdHistory(Base):
    __tablename__ = "threshold_history"

    id = Column(Integer, primary_key=True)
    inventory_id = Column(Integer, ForeignKey("inventory.id"))
    old_value = Column(Integer)
    new_value = Column(Integer)
    changed_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)


class LowStockAlert(Base):
    __tablename__ = "low_stock_alerts"

    id = Column(Integer, primary_key=True)
    inventory_id = Column(Integer, ForeignKey("inventory.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    available_qty = Column(Integer)
    threshold = Column(Integer)
    status = Column(String(20), default="open")  # open / resolved
    created_at = Column(DateTime, default=utcnow)
    resolved_at = Column(DateTime)