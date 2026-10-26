from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.utils import utcnow


class Cart(Base):
    __tablename__ = "carts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    coupon_code = Column(String(40), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    items = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")


class CartItem(Base):
    __tablename__ = "cart_items"

    id = Column(Integer, primary_key=True)
    cart_id = Column(Integer, ForeignKey("carts.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    added_at = Column(DateTime, default=utcnow)

    cart = relationship("Cart", back_populates="items")
    product = relationship("Product")
    variant = relationship("ProductVariant")


class WishlistItem(Base):
    __tablename__ = "wishlist_items"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    product = relationship("Product")