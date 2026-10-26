from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, event

from app.core.database import Base
from app.core.utils import utcnow


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String(60), nullable=False)
    entity_type = Column(String(40), nullable=False, index=True)
    entity_id = Column(Integer)
    old_value = Column(Text)
    new_value = Column(Text)
    ip_address = Column(String(50))
    created_at = Column(DateTime, default=utcnow, index=True)


# audit rows can only be inserted. any update/delete through the ORM is blocked here
@event.listens_for(AuditLog, "before_update")
def _block_audit_update(mapper, connection, target):
    raise ValueError("Audit logs cannot be modified")


@event.listens_for(AuditLog, "before_delete")
def _block_audit_delete(mapper, connection, target):
    raise ValueError("Audit logs cannot be deleted")


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id = Column(Integer, primary_key=True)
    job_type = Column(String(50), nullable=False)  # report_export, daily_summary, notification_retry
    status = Column(String(20), default="queued")  # queued / running / success / failed
    payload = Column(Text)
    result = Column(Text)
    file_path = Column(String(500))
    error = Column(String(500))
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)