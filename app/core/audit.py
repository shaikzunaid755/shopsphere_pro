from app.core.utils import to_json
from app.models import AuditLog, UserActivity


def audit(db, user, action, entity_type, entity_id=None, old=None, new=None, ip=None):
    # just adds the row, the caller commits with its own transaction
    log = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=to_json(old) if old is not None else None,
        new_value=to_json(new) if new is not None else None,
        ip_address=ip,
    )
    db.add(log)
    return log


def track_activity(db, user_id, action, details=None, ip=None):
    db.add(UserActivity(user_id=user_id, action=action, details=details, ip_address=ip))