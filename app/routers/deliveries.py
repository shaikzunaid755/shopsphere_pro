# Module 35: Delivery Management
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404, check_owner_or_staff
from app.core.roles import SUPER_ADMIN, ADMIN, ORDER_MANAGER, DELIVERY_MANAGER
from app.core.utils import utcnow
from app.models import Order, Shipment, Delivery, DeliveryAttempt, User
from app.routers.tracking import apply_status
from app.schemas.shipping import DeliveryAssign, DeliveryReassign, DeliveryConfirm, DeliveryFail, DeliveryOut

router = APIRouter(prefix="/deliveries", tags=["35 Delivery Management"])

delivery_staff = require_roles(ADMIN, ORDER_MANAGER, DELIVERY_MANAGER)
STAFF_ROLES = (SUPER_ADMIN, ADMIN, ORDER_MANAGER, DELIVERY_MANAGER)
# roles that can actually go out with the parcel
RIDER_ROLES = (DELIVERY_MANAGER, ORDER_MANAGER)
MAX_ATTEMPTS = 3
OPEN_STATUSES = ("assigned", "out_for_delivery", "failed")


def _check_access(user: User, owner_id: int):
    if is_customer(user):
        check_owner_or_staff(user, owner_id)
    elif user.role_name not in STAFF_ROLES:
        raise HTTPException(403, "You don't have permission for this action")


def _locked_delivery(db, delivery_id):
    d = db.query(Delivery).filter(Delivery.id == delivery_id).with_for_update().first()
    if not d:
        raise HTTPException(404, "Delivery not found")
    return d


def _pick_assignee(db, shipment, assigned_user_id, use_courier):
    """returns (user_id, display name). exactly one of the two options must be used"""
    if use_courier == bool(assigned_user_id):
        raise HTTPException(400, "Give assigned_user_id or set use_courier=true (only one)")
    if use_courier:
        if not shipment.provider:
            raise HTTPException(400, "Shipment has no courier assigned")
        return None, shipment.provider.name
    person = db.get(User, assigned_user_id)
    if not person or person.role_name not in RIDER_ROLES:
        raise HTTPException(400, "Delivery can be assigned only to delivery or order staff")
    if person.status != "active":
        raise HTTPException(400, f"{person.full_name} is not active")
    return person.id, person.full_name


# ---------------- assign ----------------

@router.post("", response_model=DeliveryOut, status_code=201)
def assign_delivery(data: DeliveryAssign, user: User = Depends(delivery_staff), db: Session = Depends(get_db)):
    shipment = get_or_404(db, Shipment, data.shipment_id, "Shipment")
    if shipment.status not in ("dispatched", "in_transit", "out_for_delivery", "exception"):
        raise HTTPException(400, f"Shipment is '{shipment.status}', it must be dispatched before delivery")
    if db.query(Delivery).filter(Delivery.shipment_id == shipment.id).first():
        raise HTTPException(409, "Delivery already assigned for this shipment, use reassign")

    person_id, name = _pick_assignee(db, shipment, data.assigned_user_id, data.use_courier)
    delivery = Delivery(shipment_id=shipment.id, order_id=shipment.order_id, assigned_user_id=person_id,
                        assignee_name=name, status="assigned", attempts_count=0, assigned_by=user.id)
    db.add(delivery)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Delivery already assigned for this shipment")
    audit(db, user, "delivery_assigned", "delivery", delivery.id, None, {"assignee": name})
    db.commit()
    db.refresh(delivery)
    return delivery


@router.patch("/{delivery_id:int}/reassign", response_model=DeliveryOut)
def reassign_delivery(delivery_id: int, data: DeliveryReassign, user: User = Depends(delivery_staff),
                      db: Session = Depends(get_db)):
    delivery = _locked_delivery(db, delivery_id)
    if delivery.status not in OPEN_STATUSES:
        raise HTTPException(400, f"Delivery is already {delivery.status}")
    shipment = db.get(Shipment, delivery.shipment_id)
    person_id, name = _pick_assignee(db, shipment, data.assigned_user_id, data.use_courier)
    if person_id == delivery.assigned_user_id and name == delivery.assignee_name:
        raise HTTPException(400, "Delivery is already with this person")

    old = {"assignee": delivery.assignee_name}
    delivery.assigned_user_id = person_id
    delivery.assignee_name = name
    if delivery.status == "out_for_delivery":
        delivery.status = "assigned"   # new person has to pick it up again
    audit(db, user, "delivery_reassigned", "delivery", delivery.id, old, {"assignee": name, "reason": data.reason})
    db.commit()
    db.refresh(delivery)
    return delivery


@router.post("/{delivery_id:int}/out-for-delivery", response_model=DeliveryOut)
def start_delivery(delivery_id: int, user: User = Depends(delivery_staff), db: Session = Depends(get_db)):
    delivery = _locked_delivery(db, delivery_id)
    if delivery.status not in ("assigned", "failed"):
        raise HTTPException(400, f"Delivery is '{delivery.status}', can't start it now")
    if delivery.attempts_count >= MAX_ATTEMPTS:
        raise HTTPException(400, f"Already tried {MAX_ATTEMPTS} times, return the parcel to origin")

    delivery.status = "out_for_delivery"
    shipment = db.get(Shipment, delivery.shipment_id)
    apply_status(db, shipment, "out_for_delivery", description=f"Out for delivery with {delivery.assignee_name}",
                 user=user)
    db.commit()
    db.refresh(delivery)
    return delivery


# ---------------- attempts / confirmation ----------------

def _new_attempt(db, delivery, status, user, reason=None, note=None):
    delivery.attempts_count = (delivery.attempts_count or 0) + 1
    db.add(DeliveryAttempt(delivery_id=delivery.id, attempt_no=delivery.attempts_count, status=status,
                           failure_reason=reason, note=note, recorded_by=user.id))


@router.post("/{delivery_id:int}/confirm", response_model=DeliveryOut)
def confirm_delivery(delivery_id: int, data: DeliveryConfirm, user: User = Depends(delivery_staff),
                     db: Session = Depends(get_db)):
    delivery = _locked_delivery(db, delivery_id)
    if delivery.status != "out_for_delivery":
        raise HTTPException(400, f"Delivery is '{delivery.status}', mark it out for delivery first")

    _new_attempt(db, delivery, "success", user, note=data.note)
    delivery.status = "delivered"
    delivery.received_by = data.received_by
    delivery.proof_note = data.proof_note
    delivery.delivered_at = utcnow()
    shipment = db.get(Shipment, delivery.shipment_id)
    # shipment + order go to delivered too
    apply_status(db, shipment, "delivered", description=f"Received by {data.received_by}", user=user)
    audit(db, user, "delivery_confirmed", "delivery", delivery.id, {"status": "out_for_delivery"},
          {"status": "delivered", "received_by": data.received_by})
    db.commit()
    db.refresh(delivery)
    return delivery


@router.post("/{delivery_id:int}/fail", response_model=DeliveryOut)
def failed_attempt(delivery_id: int, data: DeliveryFail, user: User = Depends(delivery_staff),
                   db: Session = Depends(get_db)):
    delivery = _locked_delivery(db, delivery_id)
    if delivery.status != "out_for_delivery":
        raise HTTPException(400, f"Delivery is '{delivery.status}', mark it out for delivery first")

    reason = data.failure_reason.replace("_", " ")
    _new_attempt(db, delivery, "failed", user, reason=data.failure_reason, note=data.note)
    delivery.status = "failed"
    left = MAX_ATTEMPTS - delivery.attempts_count
    text = f"Attempt {delivery.attempts_count} failed: {reason}"
    text += f" ({left} attempt(s) left)" if left > 0 else " - no attempts left, return to origin"
    shipment = db.get(Shipment, delivery.shipment_id)
    apply_status(db, shipment, "exception", description=text, user=user)
    audit(db, user, "delivery_failed", "delivery", delivery.id, None,
          {"attempt": delivery.attempts_count, "reason": data.failure_reason})
    db.commit()
    db.refresh(delivery)
    return delivery


# ---------------- lists / history ----------------

@router.get("", response_model=List[DeliveryOut])
def list_deliveries(status: Optional[str] = None, assigned_user_id: Optional[int] = None,
                    date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                    user: User = Depends(delivery_staff), db: Session = Depends(get_db)):
    q = db.query(Delivery)
    if status:
        q = q.filter(Delivery.status == status)
    if assigned_user_id:
        q = q.filter(Delivery.assigned_user_id == assigned_user_id)
    if date_from:
        q = q.filter(Delivery.created_at >= date_from)
    if date_to:
        q = q.filter(Delivery.created_at <= date_to)
    return q.order_by(Delivery.id.desc()).offset(skip).limit(limit).all()


@router.get("/my-assignments", response_model=List[DeliveryOut])
def my_assignments(include_done: bool = False, user: User = Depends(delivery_staff), db: Session = Depends(get_db)):
    """what the logged in delivery person has to deliver today"""
    q = db.query(Delivery).filter(Delivery.assigned_user_id == user.id)
    if not include_done:
        q = q.filter(Delivery.status.in_(OPEN_STATUSES))
    return q.order_by(Delivery.id).all()


@router.get("/failed")
def failed_deliveries(user: User = Depends(delivery_staff), db: Session = Depends(get_db)):
    rows = db.query(Delivery).filter(Delivery.status == "failed").order_by(Delivery.id).all()
    out = []
    for d in rows:
        last = d.attempts[-1] if d.attempts else None
        out.append({
            "delivery_id": d.id, "order_id": d.order_id, "shipment_id": d.shipment_id,
            "assignee": d.assignee_name, "attempts": d.attempts_count,
            "last_reason": last.failure_reason if last else None,
            "last_attempt_at": last.attempted_at if last else None,
            "can_retry": d.attempts_count < MAX_ATTEMPTS,
        })
    return {"failed_count": len(out), "deliveries": out}


@router.get("/{delivery_id:int}", response_model=DeliveryOut)
def get_delivery(delivery_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    delivery = get_or_404(db, Delivery, delivery_id, "Delivery")
    _check_access(user, db.get(Order, delivery.order_id).user_id)
    return delivery


@router.get("/orders/{order_id:int}", response_model=List[DeliveryOut])
def order_delivery_history(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    _check_access(user, order.user_id)
    return db.query(Delivery).filter(Delivery.order_id == order.id).order_by(Delivery.id).all()
