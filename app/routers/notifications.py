# Module 41: Notification Management
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.core.roles import ADMIN
from app.core.utils import money, utcnow
from app.models import Delivery, Inventory, Notification, Order, Payment, Product, Refund, Shipment, User
from app.schemas.notifications import NotificationOut, BroadcastCreate, EventNotify
from app.services.notification_service import notify, notify_roles, notify_customer

router = APIRouter(prefix="/notifications", tags=["41 Notification Management"])

admin_only = require_roles(ADMIN)
notify_staff = require_roles(ADMIN, "order_manager", "finance_manager", "inventory_manager")
INVENTORY_TEAM = ["admin", "inventory_manager"]
ORDER_TEAM = ["admin", "order_manager", "delivery_manager"]

ORDER_TEXT = {
    "confirmed": "Your order {num} is confirmed.",
    "processing": "Your order {num} is being packed.",
    "shipped": "Your order {num} has been shipped.",
    "delivered": "Your order {num} has been delivered. Thank you for shopping with us!",
    "cancelled": "Your order {num} has been cancelled.",
}


# ---------------- my notifications (any logged in user) ----------------

@router.get("/my", response_model=List[NotificationOut])
def my_notifications(is_read: Optional[bool] = None, notif_type: Optional[str] = None,
                     skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if is_read is not None:
        q = q.filter(Notification.is_read == is_read)
    if notif_type:
        q = q.filter(Notification.notif_type == notif_type)
    return q.order_by(Notification.id.desc()).offset(skip).limit(limit).all()


@router.get("/my/unread-count")
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (db.query(Notification.notif_type, func.count(Notification.id))
            .filter(Notification.user_id == user.id, Notification.is_read == False)  # noqa: E712
            .group_by(Notification.notif_type).all())
    by_type = {t or "system": c for t, c in rows}
    return {"unread": sum(by_type.values()), "by_type": by_type}


@router.post("/{notification_id:int}/read", response_model=NotificationOut)
def mark_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    # someone else's notification -> not found
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    if not n.is_read:
        n.is_read = True
        n.read_at = utcnow()
        db.commit()
        db.refresh(n)
    return n


@router.post("/{notification_id:int}/unread", response_model=NotificationOut)
def mark_unread(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    n.is_read = False
    n.read_at = None
    db.commit()
    db.refresh(n)
    return n


@router.post("/read-all")
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = (db.query(Notification)
             .filter(Notification.user_id == user.id, Notification.is_read == False)  # noqa: E712
             .update({"is_read": True, "read_at": utcnow()}, synchronize_session=False))
    db.commit()
    return {"marked_read": count}


# ---------------- staff side ----------------

@router.post("/send", status_code=201)
def send_notification(data: BroadcastCreate, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    sent = 0
    if data.user_id:
        target = db.get(User, data.user_id)
        if not target or target.status != "active":
            raise HTTPException(404, "User not found or not active")
        notify(db, target.id, data.title, data.message, data.notif_type)
        sent += 1
    if data.roles:
        sent += notify_roles(db, data.roles, data.title, data.message, data.notif_type)
    if not sent:
        raise HTTPException(400, "No active users found for these roles")
    audit(db, user, "notification_sent", "notification", None, None,
          {"user_id": data.user_id, "roles": data.roles, "title": data.title, "count": sent})
    db.commit()
    return {"sent_to": sent}


def _event_message(db, event, ref_id):
    """builds the customer message from the real record, returns (customer, title, text, ref_type)"""
    if event == "order_status":
        order = db.get(Order, ref_id)
        if not order:
            raise HTTPException(404, "Order not found")
        text = ORDER_TEXT.get(order.status, f"Your order {{num}} is now {order.status}.")
        return order.user_id, f"Order {order.order_number}", text.format(num=order.order_number), "order", order.id
    if event == "payment_status":
        p = db.get(Payment, ref_id)
        if not p:
            raise HTTPException(404, "Payment not found")
        order = db.get(Order, p.order_id)
        words = {"succeeded": "was successful", "failed": "failed, please try again", "pending": "is pending"}
        text = f"Payment of Rs {money(p.amount)} for order {order.order_number} {words.get(p.status, p.status)}."
        return p.user_id, "Payment update", text, "payment", p.id
    r = db.get(Refund, ref_id)
    if not r:
        raise HTTPException(404, "Refund not found")
    order = db.get(Order, r.order_id)
    text = f"Refund {r.refund_number} of Rs {money(r.amount)} for order {order.order_number} is {r.status}."
    return order.user_id, "Refund update", text, "refund", r.id


@router.post("/events", status_code=201)
def notify_customer_event(data: EventNotify, user: User = Depends(notify_staff), db: Session = Depends(get_db)):
    """order / payment / refund status -> in-app notification + email (and sms) to the customer"""
    customer_id, title, text, ref_type, ref_id = _event_message(db, data.event, data.reference_id)
    customer = db.get(User, customer_id)
    # same status already told to the customer -> don't spam
    already = (db.query(Notification)
               .filter(Notification.user_id == customer.id, Notification.reference_type == ref_type,
                       Notification.reference_id == ref_id, Notification.message == text).first())
    if already:
        raise HTTPException(409, "Customer was already notified about this status")
    notify_customer(db, customer, title, text, notif_type=ref_type, ref_type=ref_type, ref_id=ref_id,
                    template=data.event, email=True, sms=data.sms)
    audit(db, user, "customer_notified", ref_type, ref_id, None, {"event": data.event, "text": text})
    db.commit()
    return {"customer_id": customer.id, "title": title, "message": text, "email_queued": True, "sms_queued": data.sms}


def _alert(db, roles, title, text, ref_type, ref_id):
    # one unread alert per problem is enough
    exists = (db.query(Notification)
              .filter(Notification.notif_type == "alert", Notification.reference_type == ref_type,
                      Notification.reference_id == ref_id, Notification.is_read == False).first())  # noqa: E712
    if exists:
        return 0
    return notify_roles(db, roles, title, text, "alert", ref_type, ref_id)


@router.post("/staff-alerts/scan")
def scan_staff_alerts(user: User = Depends(notify_staff), db: Session = Depends(get_db)):
    """checks stock + delivery problems and alerts the right team"""
    result = {"low_stock": 0, "shipment_exceptions": 0, "failed_deliveries": 0, "notifications_created": 0}

    rows = (db.query(Inventory, Product.name).join(Product, Product.id == Inventory.product_id)
            .filter(Inventory.min_threshold > 0,
                    Inventory.quantity - Inventory.reserved <= Inventory.min_threshold).all())
    for inv, name in rows:
        result["low_stock"] += 1
        left = inv.quantity - inv.reserved
        result["notifications_created"] += _alert(
            db, INVENTORY_TEAM, "Low stock", f"{name} has only {left} left in warehouse {inv.warehouse_id} "
            f"(threshold {inv.min_threshold})", "inventory", inv.id)

    for s in db.query(Shipment).filter(Shipment.status == "exception").all():
        result["shipment_exceptions"] += 1
        result["notifications_created"] += _alert(
            db, ORDER_TEAM, "Shipment exception", f"Shipment {s.shipment_number} for order {s.order_id} "
            "has a delivery problem", "shipment", s.id)

    for d in db.query(Delivery).filter(Delivery.status == "failed").all():
        result["failed_deliveries"] += 1
        result["notifications_created"] += _alert(
            db, ORDER_TEAM, "Delivery failed", f"Delivery {d.id} for order {d.order_id} failed "
            f"({d.attempts_count} attempt(s))", "delivery", d.id)

    db.commit()
    return result
