from datetime import datetime
from decimal import Decimal
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ORMModel


# ---------- categories ----------
class CategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[int] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[int] = None


class CategoryStatus(BaseModel):
    is_active: bool


class CategoryOut(ORMModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    parent_id: Optional[int] = None
    is_active: bool


class CategoryTree(CategoryOut):
    children: List["CategoryTree"] = []


# ---------- brands ----------
class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    logo_url: Optional[str] = None


class BrandUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None


class BrandStatus(BaseModel):
    is_active: bool


class BrandOut(ORMModel):
    id: int
    name: str
    description: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: bool


# ---------- products ----------
class ProductCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: Optional[str] = None
    sku: str = Field(min_length=2, max_length=60)
    category_id: int
    brand_id: Optional[int] = None
    supplier_id: Optional[int] = None
    base_price: Decimal = Field(gt=0)
    sale_price: Optional[Decimal] = Field(default=None, gt=0)
    sale_start: Optional[datetime] = None
    sale_end: Optional[datetime] = None
    is_cod_allowed: bool = True
    status: str = "active"

    @model_validator(mode="after")
    def check_prices(self):
        if self.sale_price is not None and self.sale_price >= self.base_price:
            raise ValueError("sale_price must be less than base_price")
        if self.sale_start and self.sale_end and self.sale_end <= self.sale_start:
            raise ValueError("sale_end must be after sale_start")
        if self.status not in ("active", "inactive", "draft"):
            raise ValueError("status must be active, inactive or draft")
        return self


class ProductUpdate(BaseModel):
    # prices are changed through the pricing module so history is kept
    name: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    brand_id: Optional[int] = None
    supplier_id: Optional[int] = None
    is_cod_allowed: Optional[bool] = None


class ProductStatus(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def ok(cls, v):
        if v not in ("active", "inactive", "draft"):
            raise ValueError("status must be active, inactive or draft")
        return v


class VariantOut(ORMModel):
    id: int
    product_id: int
    sku: str
    name: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    other_option: Optional[str] = None
    price: Optional[Decimal] = None
    status: str


class ProductOut(ORMModel):
    id: int
    name: str
    slug: str
    sku: str
    description: Optional[str] = None
    category_id: int
    brand_id: Optional[int] = None
    supplier_id: Optional[int] = None
    base_price: Decimal
    sale_price: Optional[Decimal] = None
    sale_start: Optional[datetime] = None
    sale_end: Optional[datetime] = None
    is_cod_allowed: bool
    status: str
    avg_rating: float = 0
    rating_count: int = 0
    created_at: datetime


class AttrValueOut(BaseModel):
    attribute_id: int
    name: str
    value: str


class FileOut(ORMModel):
    id: int
    product_id: int
    variant_id: Optional[int] = None
    file_name: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    file_kind: str
    created_at: datetime


class VariantOut(ORMModel):
    id: int
    product_id: int
    sku: str
    name: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    other_option: Optional[str] = None
    price: Optional[Decimal] = None
    status: str


class ProductOut(ORMModel):
    id: int
    name: str
    slug: str
    sku: str
    description: Optional[str] = None
    category_id: int
    brand_id: Optional[int] = None
    supplier_id: Optional[int] = None
    base_price: Decimal
    sale_price: Optional[Decimal] = None
    sale_start: Optional[datetime] = None
    sale_end: Optional[datetime] = None
    is_cod_allowed: bool
    status: str
    avg_rating: float = 0
    rating_count: int = 0
    created_at: datetime


class AttrValueOut(BaseModel):
    attribute_id: int
    name: str
    value: str


class FileOut(ORMModel):
    id: int
    product_id: int
    variant_id: Optional[int] = None
    file_name: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    file_kind: str
    created_at: datetime


class ProductDetail(ProductOut):
    current_price: Decimal
    category_name: Optional[str] = None
    brand_name: Optional[str] = None
    in_stock: int = 0
    variants: List[VariantOut] = []
    attributes: List[AttrValueOut] = []
    files: List[FileOut] = []


class ProductList(BaseModel):
    total: int
    page: int
    size: int
    items: List[ProductOut]


# ---------- variants ----------
class VariantCreate(BaseModel):
    sku: str = Field(min_length=2, max_length=60)
    name: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    other_option: Optional[str] = None
    price: Optional[Decimal] = Field(default=None, gt=0)


class VariantUpdate(BaseModel):
    name: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    other_option: Optional[str] = None
    price: Optional[Decimal] = Field(default=None, gt=0)


class VariantStatus(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def ok(cls, v):
        if v not in ("active", "inactive"):
            raise ValueError("status must be active or inactive")
        return v


class VariantAvailability(BaseModel):
    variant_id: int
    sku: str
    status: str
    available: int
    in_stock: bool


# ---------- attributes ----------
class AttributeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    data_type: str = "text"
    allowed_values: Optional[List[str]] = None
    category_id: Optional[int] = None

    @model_validator(mode="after")
    def check(self):
        if self.data_type not in ("text", "number", "choice"):
            raise ValueError("data_type must be text, number or choice")
        if self.data_type == "choice" and not self.allowed_values:
            raise ValueError("choice attributes need allowed_values")
        return self


class AttributeUpdate(BaseModel):
    allowed_values: Optional[List[str]] = None
    category_id: Optional[int] = None
    is_active: Optional[bool] = None


class AttributeOut(ORMModel):
    id: int
    name: str
    data_type: str
    allowed_values: Optional[str] = None
    category_id: Optional[int] = None
    is_active: bool


class ProductAttributeSet(BaseModel):
    attribute_id: int
    value: str = Field(min_length=1, max_length=255)


# ---------- pricing ----------
class PriceUpdate(BaseModel):
    base_price: Optional[Decimal] = Field(default=None, gt=0)
    sale_price: Optional[Decimal] = Field(default=None, gt=0)
    sale_start: Optional[datetime] = None
    sale_end: Optional[datetime] = None
    remove_sale: bool = False
    reason: Optional[str] = None


class VariantPriceUpdate(BaseModel):
    price: Optional[Decimal] = Field(default=None, gt=0)  # null = go back to product price
    reason: Optional[str] = None


class PriceHistoryOut(ORMModel):
    id: int
    product_id: int
    variant_id: Optional[int] = None
    old_base_price: Optional[Decimal] = None
    new_base_price: Optional[Decimal] = None
    old_sale_price: Optional[Decimal] = None
    new_sale_price: Optional[Decimal] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    reason: Optional[str] = None
    changed_by: Optional[int] = None
    created_at: datetime


class CurrentPriceOut(BaseModel):
    product_id: int
    base_price: Decimal
    sale_price: Optional[Decimal] = None
    sale_active: bool
    current_price: Decimal
    sale_start: Optional[datetime] = None
    sale_end: Optional[datetime] = None