# Module 33: Shipping Order Management
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404, check_owner_or_staff
from app.core.roles import SUPER_ADMIN, ADMIN, ORDER_MANAGER, DELIVERY_MANAGER
from app.core.utils import make_number, utcnow
from app.models import Order, Shipment, ShippingProvider, TrackingEvent, User
from app.schemas.shipping import (ProviderCreate, ProviderUpdate, ProviderOut, ShipmentCreate, AssignProvider,
                                  DispatchIn, TrackingUpdate, ShipmentOut)
from app.services import order_service

router = APIRouter(prefix="/shipments", tags=["33 Shipping Order Management"])

provider_admin = require_roles(ADMIN, DELIVERY_MANAGER)
shipping_staff = require_roles(ADMIN, ORDER_MANAGER, DELIVERY_MANAGER)
STAFF_ROLES = (SUPER_ADMIN, ADMIN, ORDER_MANAGER, DELIVERY_MANAGER)

# where a shipment can go next. delivered / returned are final
NEXT_STATUS = {
    "created": ["dispatched"],
    "dispatched": ["in_transit", "out_for_delivery", "exception"],
    "in_transit": ["in_transit", "out_for_delivery", "exception"],
    "out_for_delivery": ["delivered", "exception", "in_transit"],
    "exception": ["in_transit", "out_for_delivery", "returned"],
    "delivered": [],
    "returned": [],
}


def _check_access(user: User, owner_id: int):
    if is_customer(user):
        check_owner_or_staff(user, owner_id)
    elif user.role_name not in STAFF_ROLES:
        raise HTTPException(403, "You don't have permission for this action")


def _add_event(db, shipment, status, location=None, description=None, when=None, exception=False):
    db.add(TrackingEvent(shipment_id=shipment.id, status=status, location=location, description=description,
                         is_exception=exception, source="manual", event_time=when or utcnow()))


def _locked_shipment(db, shipment_id):
    s = db.query(Shipment).filter(Shipment.id == shipment_id).with_for_update().first()
    if not s:
        raise HTTPException(404, "Shipment not found")
    return s


def _make_tracking(provider, shipment):
    # courier normally gives the AWB number, if not we build our own reference
    return f"{provider.code}{shipment.shipment_number.split('-')[-1]}{shipment.id:04d}"


def _active_provider(db, provider_id):
    p = db.get(ShippingProvider, provider_id)
    if not p:
        raise HTTPException(404, "Shipping provider not found")
    if not p.is_active:
        raise HTTPException(400, f"{p.name} is not active")
    return p


# ---------------- shipping providers ----------------

@router.post("/providers", response_model=ProviderOut, status_code=201)
def create_provider(data: ProviderCreate, user: User = Depends(provider_admin), db: Session = Depends(get_db)):
    code = data.code.upper()
    if db.query(ShippingProvider).filter(ShippingProvider.code == code).first():
        raise HTTPException(409, f"Provider code {code} already exists")
    p = ShippingProvider(name=data.name.strip(), code=code, contact_phone=data.contact_phone,
                         tracking_url=data.tracking_url, is_active=True)
    db.add(p)
    db.flush()
    audit(db, user, "provider_created", "shipping_provider", p.id, None, {"code": code})
    db.commit()
    db.refresh(p)
    return p


@router.get("/providers", response_model=List[ProviderOut])
def list_providers(active_only: bool = False, user: User = Depends(shipping_staff), db: Session = Depends(get_db)):
    q = db.query(ShippingProvider)
    if active_only:
        q = q.filter(ShippingProvider.is_active.is_(True))
    return q.order_by(ShippingProvider.name).all()


@router.put("/providers/{provider_id:int}", response_model=ProviderOut)
def update_provider(provider_id: int, data: ProviderUpdate, user: User = Depends(provider_admin),
                    db: Session = Depends(get_db)):
    p = get_or_404(db, ShippingProvider, provider_id, "Shipping provider")
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "Nothing to update")
    old = {k: getattr(p, k) for k in changes}
    for key, value in changes.items():
        setattr(p, key, value)
    audit(db, user, "provider_updated", "shipping_provider", p.id, old, changes)
    db.commit()
    db.refresh(p)
    return p


# ---------------- shipments ----------------

@router.post("", response_model=ShipmentOut, status_code=201)
def create_shipment(data: ShipmentCreate, user: User = Depends(shipping_staff), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == data.order_id).with_for_update().first()
    if not order:
        raise HTTPException(404, "Order not found")
    if order.status not in ("confirmed", "processing"):
        raise HTTPException(400, f"Shipment can be created only for confirmed orders, this one is '{order.status}'")
    # one live shipment per order. a returned one can be shipped again
    live = (db.query(Shipment)
            .filter(Shipment.order_id == order.id, Shipment.status != "returned").first())
    if live:
        raise HTTPException(409, f"Order already has shipment {live.shipment_number}")
    provider = _active_provider(db, data.provider_id) if data.provider_id else None

    shipment = Shipment(shipment_number=make_number("SHP"), order_id=order.id,
                        provider_id=provider.id if provider else None, status="created",
                        weight_kg=str(data.weight_kg) if data.weight_kg else None,
                        dispatch_note=data.dispatch_note, created_by=user.id)
    db.add(shipment)
    db.flush()
    if provider:
        shipment.tracking_number = _make_tracking(provider, shipment)
    _add_event(db, shipment, "created", description="Shipment created, packing started")
    if order.status == "confirmed":
        # packing starts as soon as a shipment exists
        order_service.change_status(db, order, "processing", user, f"Shipment {shipment.shipment_number} created")
    audit(db, user, "shipment_created", "shipment", shipment.id, None,
          {"order": order.id, "shipment_number": shipment.shipment_number})
    db.commit()
    db.refresh(shipment)
    return shipment


@router.patch("/{shipment_id:int}/provider", response_model=ShipmentOut)
def assign_provider(shipment_id: int, data: AssignProvider, user: User = Depends(shipping_staff),
                    db: Session = Depends(get_db)):
    shipment = _locked_shipment(db, shipment_id)
    if shipment.status != "created":
        raise HTTPException(400, f"Provider can't be changed after dispatch (shipment is {shipment.status})")
    provider = _active_provider(db, data.provider_id)

    tracking = data.tracking_number or _make_tracking(provider, shipment)
    taken = db.query(Shipment).filter(Shipment.tracking_number == tracking, Shipment.id != shipment.id).first()
    if taken:
        raise HTTPException(409, f"Tracking number {tracking} is already used by {taken.shipment_number}")

    old = {"provider_id": shipment.provider_id, "tracking_number": shipment.tracking_number}
    shipment.provider_id = provider.id
    shipment.tracking_number = tracking
    _add_event(db, shipment, "created", description=f"Assigned to {provider.name}, tracking {tracking}")
    audit(db, user, "shipment_provider_assigned", "shipment", shipment.id, old,
          {"provider_id": provider.id, "tracking_number": tracking})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Tracking number already used")
    db.refresh(shipment)
    return shipment


@router.post("/{shipment_id:int}/dispatch", response_model=ShipmentOut)
def dispatch_shipment(shipment_id: int, data: DispatchIn, user: User = Depends(shipping_staff),
                      db: Session = Depends(get_db)):
    shipment = _locked_shipment(db, shipment_id)
    if shipment.status != "created":
        raise HTTPException(400, f"Shipment is already {shipment.status}")
    if not shipment.provider_id or not shipment.tracking_number:
        raise HTTPException(400, "Assign a shipping provider before dispatch")
    order = db.get(Order, shipment.order_id)
    if order.status == "cancelled":
        raise HTTPException(400, "Order was cancelled, can't dispatch")

    provider = db.get(ShippingProvider, shipment.provider_id)
    shipment.status = "dispatched"
    shipment.dispatched_at = utcnow()
    if data.dispatch_note:
        shipment.dispatch_note = data.dispatch_note
    if data.weight_kg:
        shipment.weight_kg = str(data.weight_kg)
    _add_event(db, shipment, "dispatched", location=data.location,
               description=f"Handed over to {provider.name}")
    # order -> shipped: this also takes the reserved stock out of the warehouse
    if order.status == "processing":
        order_service.change_status(db, order, "shipped", user,
                                    f"Dispatched via {provider.name}, tracking {shipment.tracking_number}")
    audit(db, user, "shipment_dispatched", "shipment", shipment.id, {"status": "created"},
          {"status": "dispatched"})
    db.commit()
    db.refresh(shipment)
    return shipment


@router.post("/{shipment_id:int}/tracking", response_model=ShipmentOut)
def add_tracking_update(shipment_id: int, data: TrackingUpdate, user: User = Depends(shipping_staff),
                        db: Session = Depends(get_db)):
    shipment = _locked_shipment(db, shipment_id)
    allowed = NEXT_STATUS.get(shipment.status, [])
    if data.status not in allowed:
        raise HTTPException(400, f"Can't move shipment from '{shipment.status}' to '{data.status}'")
    if data.event_time and data.event_time > utcnow():
        raise HTTPException(400, "Event time can't be in the future")

    old = shipment.status
    shipment.status = data.status
    _add_event(db, shipment, data.status, data.location, data.description, data.event_time,
               exception=data.status == "exception")
    if data.status == "delivered":
        shipment.delivered_at = data.event_time or utcnow()
        order = db.get(Order, shipment.order_id)
        if order.status == "shipped":
            order_service.change_status(db, order, "delivered", user, f"Delivered, {shipment.shipment_number}")
    audit(db, user, "shipment_status_change", "shipment", shipment.id, {"status": old}, {"status": data.status})
    db.commit()
    db.refresh(shipment)
    return shipment


@router.get("", response_model=List[ShipmentOut])
def list_shipments(status: Optional[str] = None, provider_id: Optional[int] = None, order_id: Optional[int] = None,
                   date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                   skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                   user: User = Depends(shipping_staff), db: Session = Depends(get_db)):
    q = db.query(Shipment)
    if status:
        q = q.filter(Shipment.status == status)
    if provider_id:
        q = q.filter(Shipment.provider_id == provider_id)
    if order_id:
        q = q.filter(Shipment.order_id == order_id)
    if date_from:
        q = q.filter(Shipment.created_at >= date_from)
    if date_to:
        q = q.filter(Shipment.created_at <= date_to)
    return q.order_by(Shipment.id.desc()).offset(skip).limit(limit).all()


@router.get("/{shipment_id:int}", response_model=ShipmentOut)
def get_shipment(shipment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shipment = get_or_404(db, Shipment, shipment_id, "Shipment")
    _check_access(user, db.get(Order, shipment.order_id).user_id)
    return shipment


@router.get("/orders/{order_id:int}", response_model=List[ShipmentOut])
def order_shipments(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    _check_access(user, order.user_id)
    return db.query(Shipment).filter(Shipment.order_id == order.id).order_by(Shipment.id).all()


@router.get("/track/{tracking_number}")
def track(tracking_number: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shipment = db.query(Shipment).filter(Shipment.tracking_number == tracking_number).first()
    if not shipment:
        raise HTTPException(404, "No shipment with this tracking number")
    order = db.get(Order, shipment.order_id)
    _check_access(user, order.user_id)
    provider = shipment.provider
    link = None
    if provider and provider.tracking_url:
        link = provider.tracking_url.replace("{tracking}", tracking_number)
    return {
        "shipment_number": shipment.shipment_number,
        "order_number": order.order_number,
        "provider": provider.name if provider else None,
        "tracking_number": tracking_number,
        "tracking_link": link,
        "current_status": shipment.status,
        "dispatched_at": shipment.dispatched_at,
        "delivered_at": shipment.delivered_at,
        "events": [{"status": e.status, "location": e.location, "description": e.description,
                    "time": e.event_time} for e in shipment.events],
    }
