# Module 5: Address Management
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.roles import CUSTOMER, ADMIN, ORDER_MANAGER, SUPPORT_AGENT
from app.models import Address, User, Order
from app.schemas.common import Message
from app.schemas.order import OrderOut
from app.schemas.user import AddressCreate, AddressUpdate, AddressOut

router = APIRouter(prefix="/addresses", tags=["05 Address Management"])

customer_only = require_roles(CUSTOMER)


def _my_address(db, user, address_id):
    addr = db.get(Address, address_id)
    if not addr or addr.user_id != user.id or addr.is_deleted:
        raise HTTPException(404, "Address not found")
    return addr


def _clear_default(db, user_id, address_type):
    db.query(Address).filter(Address.user_id == user_id, Address.address_type == address_type,
                             Address.is_default == True).update({"is_default": False})  # noqa


@router.post("", response_model=AddressOut, status_code=201)
def add_address(data: AddressCreate, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    count = db.query(Address).filter(Address.user_id == user.id, Address.is_deleted == False).count()  # noqa
    if count >= 10:
        raise HTTPException(400, "You can save up to 10 addresses")
    has_default = db.query(Address).filter(Address.user_id == user.id, Address.address_type == data.address_type,
                                           Address.is_default == True, Address.is_deleted == False).first()  # noqa
    make_default = data.is_default or not has_default  # first address becomes default
    if make_default:
        _clear_default(db, user.id, data.address_type)
    addr = Address(user_id=user.id, **data.model_dump(exclude={"is_default"}), is_default=make_default)
    db.add(addr)
    db.commit()
    db.refresh(addr)
    return addr


@router.get("", response_model=List[AddressOut])
def my_addresses(address_type: Optional[str] = None, user: User = Depends(customer_only),
                 db: Session = Depends(get_db)):
    q = db.query(Address).filter(Address.user_id == user.id, Address.is_deleted == False)  # noqa
    if address_type:
        q = q.filter(Address.address_type == address_type)
    return q.order_by(Address.is_default.desc(), Address.id).all()


@router.get("/{address_id}", response_model=AddressOut)
def get_address(address_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    return _my_address(db, user, address_id)


@router.put("/{address_id}", response_model=AddressOut)
def update_address(address_id: int, data: AddressUpdate, user: User = Depends(customer_only),
                   db: Session = Depends(get_db)):
    addr = _my_address(db, user, address_id)
    changes = data.model_dump(exclude_unset=True)
    for field in ("full_name", "line1", "city", "state", "postal_code", "phone"):
        if field in changes and not changes[field]:
            raise HTTPException(400, f"{field} is required and cannot be empty")
    if "address_type" in changes and changes["address_type"] != addr.address_type and addr.is_default:
        addr.is_default = False
    for k, v in changes.items():
        setattr(addr, k, v)
    db.commit()
    db.refresh(addr)
    return addr


@router.patch("/{address_id}/default", response_model=AddressOut)
def set_default(address_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    addr = _my_address(db, user, address_id)
    _clear_default(db, user.id, addr.address_type)
    addr.is_default = True
    db.commit()
    db.refresh(addr)
    return addr


@router.delete("/{address_id}", response_model=Message)
def delete_address(address_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    addr = _my_address(db, user, address_id)
    open_order = db.query(Order).filter(
        (Order.shipping_address_id == addr.id) | (Order.billing_address_id == addr.id),
        Order.status.in_(["pending", "confirmed", "processing", "shipped"])).first()
    if open_order:
        raise HTTPException(400, f"Address is used by active order {open_order.order_number}")
    # soft delete keeps old orders pointing to a valid row
    addr.is_deleted = True
    was_default = addr.is_default
    addr.is_default = False
    if was_default:
        nxt = db.query(Address).filter(Address.user_id == user.id, Address.address_type == addr.address_type,
                                       Address.is_deleted == False, Address.id != addr.id).first()  # noqa
        if nxt:
            nxt.is_default = True
    db.commit()
    return {"message": "Address deleted"}


@router.get("/{address_id}/orders", response_model=List[OrderOut], summary="Orders shipped/billed to this address")
def address_orders(address_id: int, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    addr = db.get(Address, address_id)
    if not addr or addr.user_id != user.id:
        raise HTTPException(404, "Address not found")
    return db.query(Order).filter((Order.shipping_address_id == addr.id) |
                                  (Order.billing_address_id == addr.id)).all()


@router.get("/user/{user_id}", response_model=List[AddressOut], summary="Staff: addresses of a customer")
def user_addresses(user_id: int, db: Session = Depends(get_db),
                   staff: User = Depends(require_roles(ADMIN, ORDER_MANAGER, SUPPORT_AGENT))):
    return db.query(Address).filter(Address.user_id == user_id, Address.is_deleted == False).all()  # noqa