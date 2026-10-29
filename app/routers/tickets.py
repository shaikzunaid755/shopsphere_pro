# Module 40: Customer Support Tickets
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles, is_customer, get_or_404
from app.core.roles import SUPER_ADMIN, CUSTOMER, ADMIN
from app.core.utils import make_number, utcnow
from app.models import AuditLog, Order, Product, SupportTicket, TicketMessage, User
from app.schemas.tickets import (TicketCreate, TicketAssign, TicketPriority, TicketMessageCreate, TicketResolve,
                                 TicketReason, TicketOut, TicketDetail)

router = APIRouter(prefix="/tickets", tags=["40 Customer Support Tickets"])

customer_only = require_roles(CUSTOMER)
support_staff = require_roles(ADMIN, "support_agent")
STAFF_ROLES = (SUPER_ADMIN, ADMIN, "support_agent")
AGENT_ROLES = ("support_agent", ADMIN, SUPER_ADMIN)

# open -> in_progress -> waiting_customer -> resolved -> closed  (resolved can be reopened)
OPEN_STATUSES = ("open", "in_progress", "waiting_customer")


def _is_staff(user):
    return user.role_name in STAFF_ROLES


def _ticket_for(db, ticket_id, user, lock=False):
    """customer gets only his own tickets, other roles get 403"""
    q = db.query(SupportTicket).filter(SupportTicket.id == ticket_id)
    if lock:
        q = q.with_for_update()
    t = q.first()
    if not t or (is_customer(user) and t.user_id != user.id):
        raise HTTPException(404, "Ticket not found")
    if not is_customer(user) and not _is_staff(user):
        raise HTTPException(403, "You don't have permission for this action")
    return t


def _set_status(db, t, new_status, user, extra=None):
    old = t.status
    t.status = new_status
    t.updated_at = utcnow()
    audit(db, user, f"ticket_{new_status}", "support_ticket", t.id, {"status": old},
          {"status": new_status, **(extra or {})})


def _detail(db, t, user):
    q = db.query(TicketMessage).filter(TicketMessage.ticket_id == t.id)
    if not _is_staff(user):
        q = q.filter(TicketMessage.is_internal == False)  # noqa: E712
    messages = []
    for m in q.order_by(TicketMessage.id).all():
        sender = db.get(User, m.sender_id) if m.sender_id else None
        name = sender.full_name if sender else "System"
        if sender and sender.role_name != CUSTOMER:
            name = f"{sender.full_name} (Support)"
        messages.append({"id": m.id, "sender_id": m.sender_id, "sender_name": name, "message": m.message,
                         "is_internal": bool(m.is_internal), "created_at": m.created_at})
    data = TicketOut.model_validate(t).model_dump()
    return TicketDetail(**data, messages=messages)


# ---------------- customer side ----------------

@router.post("", response_model=TicketDetail, status_code=201)
def create_ticket(data: TicketCreate, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    order = None
    if data.order_id:
        order = db.get(Order, data.order_id)
        # other customer's order -> same answer as not found
        if not order or order.user_id != user.id:
            raise HTTPException(404, "Order not found")
    if data.product_id:
        product = db.get(Product, data.product_id)
        if not product:
            raise HTTPException(404, "Product not found")
        if order and data.product_id not in [i.product_id for i in order.items]:
            raise HTTPException(400, "This product is not part of the selected order")

    t = SupportTicket(ticket_number=make_number("TKT"), user_id=user.id, order_id=data.order_id,
                      product_id=data.product_id, subject=data.subject.strip(),
                      description=data.description.strip(), category=data.category, priority=data.priority,
                      status="open", created_at=utcnow(), updated_at=utcnow())
    db.add(t)
    db.flush()
    audit(db, user, "ticket_created", "support_ticket", t.id, None,
          {"order": data.order_id, "product": data.product_id, "priority": data.priority})
    db.commit()
    db.refresh(t)
    return _detail(db, t, user)


@router.get("/my", response_model=List[TicketOut])
def my_tickets(status: Optional[str] = None, user: User = Depends(customer_only), db: Session = Depends(get_db)):
    q = db.query(SupportTicket).filter(SupportTicket.user_id == user.id)
    if status:
        q = q.filter(SupportTicket.status == status)
    return q.order_by(SupportTicket.id.desc()).all()


# ---------------- staff side ----------------

@router.get("", response_model=List[TicketOut])
def list_tickets(status: Optional[str] = None, priority: Optional[str] = None, category: Optional[str] = None,
                 assigned_to: Optional[int] = None, assigned_to_me: bool = False, unassigned: bool = False,
                 order_id: Optional[int] = None, skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                 user: User = Depends(support_staff), db: Session = Depends(get_db)):
    q = db.query(SupportTicket)
    if status:
        q = q.filter(SupportTicket.status == status)
    if priority:
        q = q.filter(SupportTicket.priority == priority)
    if category:
        q = q.filter(SupportTicket.category == category)
    if assigned_to_me:
        q = q.filter(SupportTicket.assigned_to == user.id)
    elif assigned_to:
        q = q.filter(SupportTicket.assigned_to == assigned_to)
    if unassigned:
        q = q.filter(SupportTicket.assigned_to.is_(None))
    if order_id:
        q = q.filter(SupportTicket.order_id == order_id)
    return q.order_by(SupportTicket.id.desc()).offset(skip).limit(limit).all()


@router.get("/{ticket_id:int}", response_model=TicketDetail)
def get_ticket(ticket_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = _ticket_for(db, ticket_id, user)
    return _detail(db, t, user)


@router.post("/{ticket_id:int}/assign", response_model=TicketDetail)
def assign_ticket(ticket_id: int, data: TicketAssign, user: User = Depends(support_staff),
                  db: Session = Depends(get_db)):
    t = _ticket_for(db, ticket_id, user, lock=True)
    if t.status not in OPEN_STATUSES:
        raise HTTPException(400, f"Ticket is {t.status}, it can't be assigned")
    agent = db.get(User, data.agent_id)
    if not agent or agent.role_name not in AGENT_ROLES:
        raise HTTPException(400, "Tickets can be assigned only to support agents")
    if agent.status != "active":
        raise HTTPException(400, f"{agent.full_name} is not active")
    if t.assigned_to == agent.id:
        raise HTTPException(400, f"Ticket is already with {agent.full_name}")
    old_agent = t.assigned_to
    t.assigned_to = agent.id
    audit(db, user, "ticket_assigned", "support_ticket", t.id, {"assigned_to": old_agent},
          {"assigned_to": agent.id, "note": data.note})
    if t.status == "open":
        _set_status(db, t, "in_progress", user)
    t.updated_at = utcnow()
    db.commit()
    db.refresh(t)
    return _detail(db, t, user)


@router.patch("/{ticket_id:int}/priority", response_model=TicketDetail)
def change_priority(ticket_id: int, data: TicketPriority, user: User = Depends(support_staff),
                    db: Session = Depends(get_db)):
    t = _ticket_for(db, ticket_id, user, lock=True)
    if t.status not in OPEN_STATUSES:
        raise HTTPException(400, f"Ticket is {t.status}, priority can't be changed")
    if t.priority == data.priority:
        raise HTTPException(400, f"Priority is already {data.priority}")
    audit(db, user, "ticket_priority_changed", "support_ticket", t.id, {"priority": t.priority},
          {"priority": data.priority, "reason": data.reason})
    t.priority = data.priority
    t.updated_at = utcnow()
    db.commit()
    db.refresh(t)
    return _detail(db, t, user)


@router.post("/{ticket_id:int}/messages", response_model=TicketDetail, status_code=201)
def add_message(ticket_id: int, data: TicketMessageCreate, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    t = _ticket_for(db, ticket_id, user, lock=True)
    if t.status == "closed":
        raise HTTPException(400, "Ticket is closed, please create a new ticket")
    staff = _is_staff(user)
    if data.is_internal and not staff:
        raise HTTPException(403, "Only support staff can add internal notes")
    db.add(TicketMessage(ticket_id=t.id, sender_id=user.id, message=data.message.strip(),
                         is_internal=data.is_internal, created_at=utcnow()))
    # conversation moves the status along
    if not staff and t.status in ("waiting_customer", "resolved"):
        _set_status(db, t, "in_progress" if t.assigned_to else "open", user, {"by": "customer reply"})
        t.resolved_at = None
    elif staff and not data.is_internal and t.status in ("open", "in_progress"):
        _set_status(db, t, "waiting_customer", user, {"by": "agent reply"})
    t.updated_at = utcnow()
    db.commit()
    db.refresh(t)
    return _detail(db, t, user)


@router.post("/{ticket_id:int}/resolve", response_model=TicketDetail)
def resolve_ticket(ticket_id: int, data: TicketResolve, user: User = Depends(support_staff),
                   db: Session = Depends(get_db)):
    t = _ticket_for(db, ticket_id, user, lock=True)
    if t.status not in OPEN_STATUSES:
        raise HTTPException(400, f"Ticket is already {t.status}")
    t.resolution = data.resolution.strip()
    t.resolved_at = utcnow()
    if not t.assigned_to:
        t.assigned_to = user.id
    db.add(TicketMessage(ticket_id=t.id, sender_id=user.id, message=f"Resolution: {t.resolution}",
                         is_internal=False, created_at=utcnow()))
    _set_status(db, t, "resolved", user, {"resolution": t.resolution})
    db.commit()
    db.refresh(t)
    return _detail(db, t, user)


@router.post("/{ticket_id:int}/close", response_model=TicketDetail)
def close_ticket(ticket_id: int, data: TicketReason, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """customer accepts the resolution, or staff closes it"""
    t = _ticket_for(db, ticket_id, user, lock=True)
    if t.status != "resolved":
        raise HTTPException(400, f"Only resolved tickets can be closed (this one is {t.status})")
    _set_status(db, t, "closed", user, {"reason": data.reason})
    db.commit()
    db.refresh(t)
    return _detail(db, t, user)


@router.post("/{ticket_id:int}/reopen", response_model=TicketDetail)
def reopen_ticket(ticket_id: int, data: TicketReason, user: User = Depends(customer_only),
                  db: Session = Depends(get_db)):
    t = _ticket_for(db, ticket_id, user, lock=True)
    if t.status != "resolved":
        raise HTTPException(400, f"Only resolved tickets can be reopened (this one is {t.status})")
    if not data.reason or len(data.reason.strip()) < 5:
        raise HTTPException(400, "Please tell us why the issue is not solved (min 5 characters)")
    db.add(TicketMessage(ticket_id=t.id, sender_id=user.id, message=f"Reopened: {data.reason.strip()}",
                         is_internal=False, created_at=utcnow()))
    t.resolved_at = None
    _set_status(db, t, "in_progress" if t.assigned_to else "open", user, {"reason": data.reason})
    db.commit()
    db.refresh(t)
    return _detail(db, t, user)


def _load(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


@router.get("/{ticket_id:int}/history")
def ticket_history(ticket_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """status / priority / assignment changes + the conversation, oldest first"""
    t = _ticket_for(db, ticket_id, user)
    logs = (db.query(AuditLog).filter(AuditLog.entity_type == "support_ticket", AuditLog.entity_id == t.id)
            .order_by(AuditLog.id).all())
    events = [{"action": log.action, "by": log.user_id, "old": _load(log.old_value), "new": _load(log.new_value),
               "at": log.created_at} for log in logs]
    detail = _detail(db, t, user)
    return {"ticket_number": t.ticket_number, "status": t.status, "priority": t.priority,
            "assigned_to": t.assigned_to, "resolution": t.resolution, "resolved_at": t.resolved_at,
            "events": events, "conversation": detail.messages}
