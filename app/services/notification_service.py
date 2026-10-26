import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.core.utils import utcnow, from_json
from app.models import Notification, OutboundMessage, MessageProvider, User, Role

log = logging.getLogger("shopsphere.notify")


def notify(db, user_id, title, message, notif_type="system", ref_type=None, ref_id=None):
    n = Notification(user_id=user_id, title=title, message=message, notif_type=notif_type,
                     reference_type=ref_type, reference_id=ref_id)
    db.add(n)
    return n


def notify_roles(db, role_names, title, message, notif_type="system", ref_type=None, ref_id=None):
    # in-app alert for every active staff user with one of these roles
    staff = (db.query(User).join(Role, Role.id == User.role_id)
             .filter(Role.name.in_(role_names), User.status == "active").all())
    for s in staff:
        notify(db, s.id, title, message, notif_type, ref_type, ref_id)
    return len(staff)


def queue_message(db, channel, recipient, body, subject=None, template=None, user_id=None):
    if not recipient:
        return None
    provider = (db.query(MessageProvider)
                .filter(MessageProvider.channel == channel, MessageProvider.is_active == True)  # noqa: E712
                .first())
    msg = OutboundMessage(channel=channel, recipient=recipient, subject=subject, body=body,
                          template=template, user_id=user_id,
                          provider_id=provider.id if provider else None)
    db.add(msg)
    return msg


def notify_customer(db, user, title, message, notif_type="order", ref_type=None, ref_id=None,
                    template=None, email=True, sms=False):
    """in-app + email (+ sms) in one go, used by order/payment/shipping flows"""
    notify(db, user.id, title, message, notif_type, ref_type, ref_id)
    if email:
        queue_message(db, "email", user.email, message, subject=title, template=template, user_id=user.id)
    if sms and user.phone:
        queue_message(db, "sms", user.phone, f"{title}: {message}", template=template, user_id=user.id)


def _send_email(provider, msg):
    cfg = from_json(provider.config) if provider else None
    cfg = cfg or {}
    host = cfg.get("host") or settings.SMTP_HOST
    if (provider and provider.provider_type == "console") or not host:
        log.info("EMAIL to %s | %s | %s", msg.recipient, msg.subject, msg.body)
        return
    port = int(cfg.get("port") or settings.SMTP_PORT)
    em = EmailMessage()
    em["From"] = cfg.get("from") or settings.MAIL_FROM
    em["To"] = msg.recipient
    em["Subject"] = msg.subject or "ShopSphere"
    em.set_content(msg.body)
    with smtplib.SMTP(host, port, timeout=10) as server:
        server.starttls()
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(em)


def _send_sms(provider, msg):
    # no paid sms gateway in this project, console provider just logs it.
    # a real provider (twilio, msg91) would plug in here.
    if provider and provider.provider_type not in ("console",):
        raise RuntimeError(f"SMS provider type '{provider.provider_type}' is not supported")
    log.info("SMS to %s | %s", msg.recipient, msg.body)


def send_message(db, msg: OutboundMessage):
    if msg.status == "sent":
        return msg  # never send twice
    if msg.attempts >= msg.max_attempts:
        return msg
    provider = db.get(MessageProvider, msg.provider_id) if msg.provider_id else None
    msg.attempts += 1
    try:
        if msg.channel == "email":
            _send_email(provider, msg)
        else:
            _send_sms(provider, msg)
        msg.status = "sent"
        msg.sent_at = utcnow()
        msg.last_error = None
    except Exception as e:  # want to catch smtp/network errors of any kind
        msg.status = "failed"
        msg.last_error = str(e)[:500]
    return msg


def process_outbox(db, include_failed=True):
    q = db.query(OutboundMessage).filter(OutboundMessage.attempts < OutboundMessage.max_attempts)
    if include_failed:
        q = q.filter(OutboundMessage.status.in_(["queued", "failed"]))
    else:
        q = q.filter(OutboundMessage.status == "queued")
    count = {"sent": 0, "failed": 0}
    for msg in q.limit(200).all():
        send_message(db, msg)
        count[msg.status] = count.get(msg.status, 0) + 1
    db.commit()
    return count