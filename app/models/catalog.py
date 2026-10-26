from sqlalchemy import (Column, Integer, String, Boolean, DateTime, ForeignKey, Text,
                        Numeric, Float, UniqueConstraint)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.utils import utcnow


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(120), unique=True, nullable=False)
    description = Column(String(255))
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

    parent = relationship("Category", remote_side=[id], back_populates="children")
    children = relationship("Category", back_populates="parent")


class Brand(Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(String(255))
    logo_url = Column(String(255))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    slug = Column(String(220), unique=True, nullable=False)
    description = Column(Text)
    sku = Column(String(60), unique=True, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)

    base_price = Column(Numeric(12, 2), nullable=False)
    sale_price = Column(Numeric(12, 2), nullable=True)
    sale_start = Column(DateTime, nullable=True)
    sale_end = Column(DateTime, nullable=True)

    is_cod_allowed = Column(Boolean, default=True)
    status = Column(String(20), default="active")  # active / inactive / draft
    avg_rating = Column(Float, default=0)
    rating_count = Column(Integer, default=0)

    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    category = relationship("Category")
    brand = relationship("Brand")
    variants = relationship("ProductVariant", back_populates="product")
    attribute_values = relationship("ProductAttributeValue", back_populates="product")
    files = relationship("ProductFile", back_populates="product")

    def current_price(self, now=None):
        # sale price counts only inside its validity window
        now = now or utcnow()
        if self.sale_price is not None:
            started = self.sale_start is None or self.sale_start <= now
            not_ended = self.sale_end is None or self.sale_end >= now
            if started and not_ended:
                return self.sale_price
        return self.base_price


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    sku = Column(String(60), unique=True, nullable=False)
    name = Column(String(120))
    size = Column(String(30))
    color = Column(String(30))
    other_option = Column(String(60))
    price = Column(Numeric(12, 2), nullable=True)  # null means use product price
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=utcnow)

    product = relationship("Product", back_populates="variants")


class ProductFile(Base):
    __tablename__ = "product_files"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    file_name = Column(String(255), nullable=False)
    stored_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    content_type = Column(String(100))
    size_bytes = Column(Integer)
    file_kind = Column(String(20), default="image")  # image / document
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)

    product = relationship("Product", back_populates="files")


class Attribute(Base):
    __tablename__ = "attributes"

    id = Column(Integer, primary_key=True)
    name = Column(String(60), unique=True, nullable=False)
    data_type = Column(String(20), default="text")  # text / number / choice
    allowed_values = Column(String(500))  # comma separated, for choice type
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class ProductAttributeValue(Base):
    __tablename__ = "product_attribute_values"
    __table_args__ = (UniqueConstraint("product_id", "attribute_id", name="uq_product_attr"),)

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    attribute_id = Column(Integer, ForeignKey("attributes.id"), nullable=False)
    value = Column(String(255), nullable=False)

    product = relationship("Product", back_populates="attribute_values")
    attribute = relationship("Attribute")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    old_base_price = Column(Numeric(12, 2))
    new_base_price = Column(Numeric(12, 2))
    old_sale_price = Column(Numeric(12, 2))
    new_sale_price = Column(Numeric(12, 2))
    valid_from = Column(DateTime)
    valid_to = Column(DateTime)
    reason = Column(String(255))
    changed_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=utcnow)