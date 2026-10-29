# Module 43 / 44 / 45: Suppliers, Purchase Orders, Supplier Invoices - schemas
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

GST_PATTERN = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"


class SupplierCreate(BaseModel):
    name: str = Field(min_length=3, max_length=150)
    supplier_type: Literal["supplier", "seller"] = "supplier"
    contact_person: str = Field(min_length=3, max_length=100)
    email: EmailStr
    phone: str = Field(pattern=r"^[6-9][0-9]{9}$")
    gst_number: Optional[str] = Field(None, pattern=GST_PATTERN)
    address: str = Field(min_length=10, max_length=500)


class SupplierUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=150)
    contact_person: Optional[str] = Field(None, min_length=3, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r"^[6-9][0-9]{9}$")
    gst_number: Optional[str] = Field(None, pattern=GST_PATTERN)
    address: Optional[str] = Field(None, min_length=10, max_length=500)


class SupplierDecision(BaseModel):
    action: Literal["approve", "reject", "suspend", "reactivate"]
    note: Optional[str] = Field(None, max_length=255)


class SupplierProductLink(BaseModel):
    product_id: int = Field(gt=0)
    supplier_sku: Optional[str] = Field(None, max_length=60)
    cost_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    # make this supplier the main supplier of the product
    set_primary: bool = False


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    supplier_type: Optional[str] = None
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    gst_number: Optional[str] = None
    address: Optional[str] = None
    status: Optional[str] = None
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class POItemIn(BaseModel):
    product_id: int = Field(gt=0)
    variant_id: Optional[int] = Field(None, gt=0)
    quantity: int = Field(gt=0, le=100000)
    # empty -> cost price from supplier_products
    unit_cost: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=2)


class POCreate(BaseModel):
    supplier_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    expected_date: Optional[date] = None
    note: Optional[str] = Field(None, max_length=255)
    items: List[POItemIn] = Field(min_length=1, max_length=100)


class ReceiveLine(BaseModel):
    item_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


class POReceive(BaseModel):
    items: List[ReceiveLine] = Field(min_length=1)
    note: Optional[str] = Field(None, max_length=255)


class POCancel(BaseModel):
    reason: str = Field(min_length=5, max_length=255)


class POItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    variant_id: Optional[int] = None
    ordered_qty: int
    received_qty: int
    unit_cost: Decimal


class POOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    po_number: str
    supplier_id: int
    warehouse_id: int
    status: str
    expected_date: Optional[date] = None
    total_amount: Optional[Decimal] = None
    note: Optional[str] = None
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    items: List[POItemOut] = []


class InvoiceCreate(BaseModel):
    invoice_number: str = Field(min_length=3, max_length=60)
    supplier_id: int = Field(gt=0)
    po_id: int = Field(gt=0)
    invoice_date: date
    due_date: date
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    total_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class InvoicePayment(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reference: str = Field(min_length=3, max_length=60)


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    supplier_id: int
    po_id: Optional[int] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    paid_amount: Decimal
    payment_status: str
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
