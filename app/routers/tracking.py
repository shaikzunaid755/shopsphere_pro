# Module 34: Shipment Tracking
import hashlib
import hmac
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404, check_owner_or_staff
from app.core.roles import SUPER_ADMIN, CUSTOMER, ADMIN, ORDER_MANAGER, DELIVERY_MANAGER
from app.core.utils import utcnow
from app.models import Order, Shipment, ShippingProvider, TrackingEvent, WebhookEvent, User
from app.routers.shipments import NEXT_STATUS
from app.schemas.shipping import (ProviderTrackingUpdate, TrackingNumberUpdate, ExceptionRaise, ExceptionResolve,
                                  ShipmentOut, TrackingEventOut)
from app.services import order_service

router = APIRouter(prefix="/tracking", tags=["34 Shipment Tracking"])

shipping_staff = require_roles(ADMIN, ORDER_MANAGER, DELIVERY_MANAGER)
customer_only = require_roles(CUSTOMER)
STAFF_ROLES = (SUPER_ADMIN, ADMIN, ORDER_MANAGER, DELIVERY_MANAGER)

# couriers don't all use the same words, map them to ours
STATUS_ALIASES = {
    "in_transit": "in_transit", "intransit": "in_transit", "reached_hub": "in_transit", "picked_up": "in_transit",
    "out_for_delivery": "out_for_delivery", "ofd": "out_for_delivery",
    "delivered": "delivered",
    "exception": "exception", "undelivered": "exception", "failed_attempt": "exception", "delayed": "exception",
    "returned": "returned", "rto": "returned", "rto_delivered": "returned",
}
LIVE_STATUSES = ("dispatched", "in_transit", "out_for_delivery")


def _check_access(user: User, owner_id: int):
    if is_customer(user):
        check_owner_or_staff(user, owner_id)
    elif user.role_name not in STAFF_ROLES:
        raise HTTPException(403, "You don't have permission for this action")


def _webhook_key(provider):
    # one secret from .env, mixed with the courier code so each courier gets its own key
    base = getattr(settings, "SHIPPING_WEBHOOK_SECRET", None) or settings.SANDBOX_WEBHOOK_SECRET
    return f"{base}:{provider.code}".encode()


def sign_tracking_payload(provider, body: bytes, timestamp: str = None):
    """same format as stripe: t=<unix time>,v1=<hmac sha256>. used by the test script too"""
    timestamp = timestamp or str(int(time.time()))
    sig = hmac.new(_webhook_key(provider), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


def _verify(provider, body: bytes, header: Optional[str]):
    if not header:
        raise HTTPException(400, "Missing signature header")
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, given = parts["t"], parts["v1"]
        ts_int = int(ts)
    except (ValueError, KeyError):
        raise HTTPException(400, "Bad signature format")
    if abs(time.time() - ts_int) > 300:
        raise HTTPException(400, "Webhook timestamp too old")
    expected = sign_tracking_payload(provider, body, ts).split("v1=")[1]
    if not hmac.compare_digest(expected, given):
        raise HTTPException(400, "Invalid webhook signature")


def _last_event(db, shipment_id):
    return (db.query(TrackingEvent).filter(TrackingEvent.shipment_id == shipment_id)
            .order_by(TrackingEvent.event_time.desc(), TrackingEvent.id.desc()).first())


def apply_status(db, shipment, new_status, location=None, description=None, when=None, source="manual", user=None):
    """saves the event in history always. changes the shipment status only when the step is valid
    and the event is not older than what we already have. returns (applied, reason)"""
    when = when or utcnow()
    last = _last_event(db, shipment.id)
    db.add(TrackingEvent(shipment_id=shipment.id, status=new_status, location=location, description=description,
                         is_exception=new_status == "exception", source=source, event_time=when))

    if new_status not in NEXT_STATUS.get(shipment.status, []):
        return False, f"kept in history only, can't go from '{shipment.status}' to '{new_status}'"
    if last and last.event_time and when < last.event_time:
        return False, "kept in history only, event is older than the latest one"

    shipment.status = new_status
    if new_status == "delivered":
        shipment.delivered_at = when
        order = db.get(Order, shipment.order_id)
        if order.status == "shipped":
            order_service.change_status(db, order, "delivered", user, f"Delivered ({source} update)")
    return True, "status updated"


def _status_view(db, shipment):
    last = _last_event(db, shipment.id)
    provider = shipment.provider
    return {
        "shipment_id": shipment.id,
        "shipment_number": shipment.shipment_number,
        "order_id": shipment.order_id,
        "tracking_number": shipment.tracking_number,
        "provider": provider.name if provider else None,
        "current_status": shipment.status,
        "has_exception": shipment.status == "exception",
        "last_update": last.event_time if last else None,
        "last_location": last.location if last else None,
        "last_description": last.description if last else None,
        "dispatched_at": shipment.dispatched_at,
        "delivered_at": shipment.delivered_at,
    }


# ---------------- provider updates (webhook) ----------------

@router.post("/webhooks/{provider_code}")
async def provider_webhook(provider_code: str, request: Request, db: Session = Depends(get_db),
                           x_signature: Optional[str] = Header(None, alias="X-Signature")):
    """couriers push their scans here. body is signed with HMAC so nobody else can fake updates"""
    provider = db.query(ShippingProvider).filter(ShippingProvider.code == provider_code.upper()).first()
    if not provider or not provider.is_active:
        raise HTTPException(404, "Unknown shipping provider")
    body = await request.body()
    _verify(provider, body, x_signature)
    try:
        data = ProviderTrackingUpdate.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(422, f"Bad payload: {e.errors()[0]['msg']}")

    # courier retries the same event -> process it only once
    event_key = f"{provider.code}:{data.event_id}"
    if db.query(WebhookEvent).filter(WebhookEvent.event_id == event_key).first():
        return {"received": True, "duplicate": True}
    db.add(WebhookEvent(event_id=event_key, event_type=f"tracking.{data.status}"[:60]))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"received": True, "duplicate": True}

    shipment = (db.query(Shipment).filter(Shipment.tracking_number == data.tracking_number)
                .with_for_update().first())
    if not shipment or shipment.provider_id != provider.id:
        db.commit()
        return {"received": True, "applied": False, "reason": "tracking number not found for this provider"}
    status = STATUS_ALIASES.get(data.status.strip().lower().replace(" ", "_"))
    if not status:
        db.commit()
        return {"received": True, "applied": False, "reason": f"unknown status '{data.status}'"}
    if data.event_time and data.event_time > utcnow():
        db.commit()
        return {"received": True, "applied": False, "reason": "event time is in the future"}

    applied, reason = apply_status(db, shipment, status, data.location, data.description, data.event_time,
                                   source="provider")
    db.commit()
    return {"received": True, "applied": applied, "reason": reason, "shipment_id": shipment.id,
            "current_status": shipment.status}


# ---------------- tracking numbers ----------------

@router.put("/shipments/{shipment_id:int}/tracking-number", response_model=ShipmentOut)
def change_tracking_number(shipment_id: int, data: TrackingNumberUpdate, user: User = Depends(shipping_staff),
                           db: Session = Depends(get_db)):
    """courier sometimes re-books the parcel with a new AWB, store the new number"""
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).with_for_update().first()
    if not shipment:
        raise HTTPException(404, "Shipment not found")
    if shipment.status in ("delivered", "returned"):
        raise HTTPException(400, f"Shipment is already {shipment.status}")
    if not shipment.provider_id:
        raise HTTPException(400, "Assign a shipping provider first")
    new_number = data.tracking_number.upper()
    if new_number == shipment.tracking_number:
        raise HTTPException(400, "That is already the tracking number")
    taken = db.query(Shipment).filter(Shipment.tracking_number == new_number).first()
    if taken:
        raise HTTPException(409, f"Tracking number already used by {taken.shipment_number}")

    old = shipment.tracking_number
    shipment.tracking_number = new_number
    # status stays the same, the change itself goes into the history
    db.add(TrackingEvent(shipment_id=shipment.id, status=shipment.status, source="manual",
                         description=f"Tracking number changed {old} -> {new_number}: {data.reason}"))
    audit(db, user, "tracking_number_changed", "shipment", shipment.id,
          {"tracking_number": old}, {"tracking_number": new_number, "reason": data.reason})
    db.commit()
    db.refresh(shipment)
    return shipment


# ---------------- current status / history ----------------

@router.get("/shipments/{shipment_id:int}/status")
def current_status(shipment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shipment = get_or_404(db, Shipment, shipment_id, "Shipment")
    _check_access(user, db.get(Order, shipment.order_id).user_id)
    return _status_view(db, shipment)


@router.get("/shipments/{shipment_id:int}/events", response_model=List[TrackingEventOut])
def event_history(shipment_id: int, source: Optional[str] = None, exceptions_only: bool = False,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shipment = get_or_404(db, Shipment, shipment_id, "Shipment")
    _check_access(user, db.get(Order, shipment.order_id).user_id)
    q = db.query(TrackingEvent).filter(TrackingEvent.shipment_id == shipment.id)
    if source:
        q = q.filter(TrackingEvent.source == source)
    if exceptions_only:
        q = q.filter(TrackingEvent.is_exception.is_(True))
    return q.order_by(TrackingEvent.event_time, TrackingEvent.id).all()


@router.get("/my")
def my_shipments(user: User = Depends(customer_only), db: Session = Depends(get_db)):
    rows = (db.query(Shipment).join(Order, Order.id == Shipment.order_id)
            .filter(Order.user_id == user.id).order_by(Shipment.id.desc()).limit(50).all())
    return [_status_view(db, s) for s in rows]


@router.get("/lookup")
def bulk_lookup(numbers: str = Query(..., description="comma separated tracking numbers, max 50"),
                user: User = Depends(shipping_staff), db: Session = Depends(get_db)):
    wanted = [n.strip().upper() for n in numbers.split(",") if n.strip()]
    if not wanted:
        raise HTTPException(400, "Give at least one tracking number")
    if len(wanted) > 50:
        raise HTTPException(400, "Maximum 50 tracking numbers at a time")
    found = {s.tracking_number: s for s in db.query(Shipment).filter(Shipment.tracking_number.in_(wanted)).all()}
    return {"found": [_status_view(db, found[n]) for n in wanted if n in found],
            "not_found": [n for n in wanted if n not in found]}


# ---------------- delivery exceptions ----------------

@router.post("/shipments/{shipment_id:int}/exception", response_model=ShipmentOut)
def raise_exception(shipment_id: int, data: ExceptionRaise, user: User = Depends(shipping_staff),
                    db: Session = Depends(get_db)):
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).with_for_update().first()
    if not shipment:
        raise HTTPException(404, "Shipment not found")
    if shipment.status not in LIVE_STATUSES:
        raise HTTPException(400, f"Exception can be raised only while the parcel is moving, it is '{shipment.status}'")
    text = data.reason.replace("_", " ")
    if data.note:
        text += f" - {data.note}"
    apply_status(db, shipment, "exception", data.location, text, source="manual", user=user)
    audit(db, user, "shipment_exception", "shipment", shipment.id, None, {"reason": data.reason, "note": data.note})
    db.commit()
    db.refresh(shipment)
    return shipment


@router.post("/shipments/{shipment_id:int}/resolve", response_model=ShipmentOut)
def resolve_exception(shipment_id: int, data: ExceptionResolve, user: User = Depends(shipping_staff),
                      db: Session = Depends(get_db)):
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).with_for_update().first()
    if not shipment:
        raise HTTPException(404, "Shipment not found")
    if shipment.status != "exception":
        raise HTTPException(400, "This shipment has no open exception")
    if data.action == "reattempt":
        apply_status(db, shipment, "out_for_delivery", description=f"Re-attempt: {data.note}", user=user)
    else:
        # parcel goes back to our warehouse, returns module takes it from here
        apply_status(db, shipment, "returned", description=f"Return to origin: {data.note}", user=user)
    audit(db, user, "shipment_exception_resolved", "shipment", shipment.id, {"status": "exception"},
          {"status": shipment.status, "action": data.action})
    db.commit()
    db.refresh(shipment)
    return shipment


@router.get("/exceptions")
def open_exceptions(user: User = Depends(shipping_staff), db: Session = Depends(get_db)):
    rows = db.query(Shipment).filter(Shipment.status == "exception").order_by(Shipment.id).all()
    now = utcnow()
    out = []
    for s in rows:
        last = (db.query(TrackingEvent)
                .filter(TrackingEvent.shipment_id == s.id, TrackingEvent.is_exception.is_(True))
                .order_by(TrackingEvent.event_time.desc(), TrackingEvent.id.desc()).first())
        view = _status_view(db, s)
        view["exception_reason"] = last.description if last else None
        view["hours_open"] = round((now - last.event_time).total_seconds() / 3600, 1) if last else None
        out.append(view)
    return {"open_exceptions": len(out), "shipments": out}
