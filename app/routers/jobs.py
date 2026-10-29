# Module 49: Report Export and Background Tasks
import logging
import os
from datetime import date, timedelta, timezone
from typing import Literal, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.database import SessionLocal, get_db
from app.core.deps import get_current_user, require_roles
from app.core.roles import ADMIN, SUPER_ADMIN
from app.core.utils import from_json, to_json, utcnow
from app.models import BackgroundJob, User
from app.services import export_service as ex
from app.services import report_service as rs
from app.services.notification_service import notify_roles, process_outbox

log = logging.getLogger("shopsphere.jobs")
router = APIRouter(tags=["49 Report Export and Background Tasks"])

admin_only = require_roles(ADMIN)
EXPORT_DIR = os.getenv("EXPORT_DIR", "exports")
ReportName = Literal["sales", "orders", "inventory", "payments", "products", "categories"]
FileFormat = Literal["csv", "xlsx", "pdf"]


class ExportJobCreate(BaseModel):
    report: ReportName
    format: FileFormat = "csv"
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    status: Optional[str] = None
    category_id: Optional[int] = None
    warehouse_id: Optional[int] = None


class TaskCreate(BaseModel):
    job_type: Literal["inventory_summary", "order_summary", "process_outbox"]


class ScheduleCreate(BaseModel):
    job_type: Literal["inventory_summary", "order_summary", "process_outbox"]
    every_minutes: int = Field(ge=1, le=1440)


def _filters(data):
    f = rs.ReportFilter(data.get("date_from"), data.get("date_to"), data.get("status"),
                        data.get("category_id"), data.get("warehouse_id"))
    f.date_from = date.fromisoformat(f.date_from) if isinstance(f.date_from, str) else f.date_from
    f.date_to = date.fromisoformat(f.date_to) if isinstance(f.date_to, str) else f.date_to
    if f.date_from and f.date_to and f.date_from > f.date_to:
        raise ValueError("date_from can't be after date_to")
    return f


def _save(name, fmt, content):
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.join(EXPORT_DIR, f"{name}_{utcnow().strftime('%Y%m%d_%H%M%S_%f')}.{ex.FORMATS[fmt][1]}")
    with open(path, "wb") as fh:
        fh.write(content)
    return path


# ---------------- job handlers: (db, payload) -> (result, file_path) ----------------

def _export(db, p):
    report = rs.run_report(db, p["report"], _filters(p))
    path = _save(p["report"], p["format"], ex.render(report, p["format"]))
    return {"rows": report["count"], "totals": report["totals"]}, path


def _inventory_summary(db, p):
    report = rs.run_report(db, "inventory", rs.ReportFilter())
    path = _save("inventory_summary", "csv", ex.to_csv(report))
    t = report["totals"]
    notify_roles(db, ["admin", "inventory_manager"], "Inventory summary ready",
                 f"{t['units']} units in stock, {t['low_stock_rows']} low stock rows", "report")
    return t, path


def _order_summary(db, p):
    yesterday = date.today() - timedelta(days=1)
    report = rs.run_report(db, "orders", rs.ReportFilter(date_from=yesterday, date_to=date.today()))
    path = _save("order_summary", "csv", ex.to_csv(report))
    t = report["totals"]
    notify_roles(db, ["admin", "order_manager"], "Order summary ready",
                 f"{t['orders']} orders worth Rs {t['value']} since yesterday", "report")
    return t, path


def _outbox(db, p):
    return process_outbox(db), None


HANDLERS = {"report_export": _export, "inventory_summary": _inventory_summary,
            "order_summary": _order_summary, "process_outbox": _outbox}


def run_job(job_id):
    """runs in the background with its own db session, every run is recorded on the job row"""
    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).with_for_update().first()
        # already running / done, or no attempts left -> nothing to do (safe to call twice)
        if not job or job.status not in ("queued", "failed") or job.attempts >= job.max_attempts:
            return
        job.status = "running"
        job.attempts += 1
        job.started_at = utcnow()
        job.error = None
        db.commit()
        try:
            result, path = HANDLERS[job.job_type](db, from_json(job.payload) or {})
            job = db.get(BackgroundJob, job_id)
            job.status = "completed"
            job.result = to_json(result)
            job.file_path = path
        except Exception as e:  # any failure is saved on the job so it can be retried
            log.exception("job %s failed", job_id)
            db.rollback()
            job = db.get(BackgroundJob, job_id)
            job.status = "failed"
            job.error = str(e)[:500]
        job.finished_at = utcnow()
        db.commit()
    finally:
        db.close()


def _new_job(db, job_type, payload, user_id):
    job = BackgroundJob(job_type=job_type, status="queued", payload=to_json(payload), attempts=0, max_attempts=3,
                        created_by=user_id, created_at=utcnow())
    db.add(job)
    db.flush()
    return job


def _job_out(job):
    return {"id": job.id, "job_type": job.job_type, "status": job.status, "payload": from_json(job.payload),
            "result": from_json(job.result), "file_ready": bool(job.file_path), "error": job.error,
            "attempts": job.attempts, "max_attempts": job.max_attempts, "created_by": job.created_by,
            "created_at": job.created_at, "started_at": job.started_at, "finished_at": job.finished_at}


# ---------------- scheduler (in memory, started on first schedule) ----------------

scheduler = BackgroundScheduler(timezone=timezone.utc)


def _scheduled_run(job_type, user_id):
    db = SessionLocal()
    try:
        job = _new_job(db, job_type, {"scheduled": True}, user_id)
        db.commit()
        job_id = job.id
    finally:
        db.close()
    run_job(job_id)


# ---------------- direct download ----------------

@router.get("/exports/{report}")
def export_report(report: ReportName, format: FileFormat = "csv", date_from: Optional[date] = None,
                  date_to: Optional[date] = None, status: Optional[str] = None, category_id: Optional[int] = None,
                  warehouse_id: Optional[int] = None, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    if not rs.can_view(user, report):
        raise HTTPException(403, f"Your role can't export the {report} report")
    try:
        f = _filters({"date_from": date_from, "date_to": date_to, "status": status, "category_id": category_id,
                      "warehouse_id": warehouse_id})
    except ValueError as e:
        raise HTTPException(400, str(e))
    data = rs.run_report(db, report, f)
    subtitle = f"From {date_from or 'start'} to {date_to or 'today'}"
    media, ext = ex.FORMATS[format]
    name = f"{report}_report_{date.today()}.{ext}"
    return Response(ex.render(data, format, subtitle), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------- background jobs ----------------

@router.post("/jobs/exports", status_code=202)
def queue_export(data: ExportJobCreate, tasks: BackgroundTasks, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """big reports: queue the export and download the file later"""
    if not rs.can_view(user, data.report):
        raise HTTPException(403, f"Your role can't export the {data.report} report")
    if data.date_from and data.date_to and data.date_from > data.date_to:
        raise HTTPException(400, "date_from can't be after date_to")
    job = _new_job(db, "report_export", data.model_dump(mode="json"), user.id)
    db.commit()
    tasks.add_task(run_job, job.id)
    return {"job_id": job.id, "status": "queued", "check": f"/jobs/{job.id}"}


@router.post("/jobs", status_code=202)
def queue_task(data: TaskCreate, tasks: BackgroundTasks, user: User = Depends(admin_only),
               db: Session = Depends(get_db)):
    job = _new_job(db, data.job_type, {}, user.id)
    audit(db, user, "job_queued", "background_job", job.id, None, {"type": data.job_type})
    db.commit()
    tasks.add_task(run_job, job.id)
    return {"job_id": job.id, "status": "queued"}


def _my_job(db, job_id, user):
    job = db.get(BackgroundJob, job_id)
    # other people's jobs look like they don't exist, admins see everything
    if not job or (user.role_name not in (ADMIN, SUPER_ADMIN) and job.created_by != user.id):
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs")
def list_jobs(status: Optional[str] = None, job_type: Optional[str] = None,
              skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(BackgroundJob)
    if user.role_name not in (ADMIN, SUPER_ADMIN):
        q = q.filter(BackgroundJob.created_by == user.id)
    if status:
        q = q.filter(BackgroundJob.status == status)
    if job_type:
        q = q.filter(BackgroundJob.job_type == job_type)
    return [_job_out(j) for j in q.order_by(BackgroundJob.id.desc()).offset(skip).limit(limit).all()]


@router.get("/jobs/{job_id:int}")
def get_job(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _job_out(_my_job(db, job_id, user))


@router.get("/jobs/{job_id:int}/download")
def download_job_file(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = _my_job(db, job_id, user)
    if job.status != "completed" or not job.file_path:
        raise HTTPException(400, f"Job is {job.status}, no file to download yet")
    if not os.path.exists(job.file_path):
        raise HTTPException(410, "File was removed from the server, run the export again")
    ext = job.file_path.rsplit(".", 1)[-1]
    media = next((m for m, e in ex.FORMATS.values() if e == ext), "application/octet-stream")
    return FileResponse(job.file_path, media_type=media, filename=os.path.basename(job.file_path))


@router.post("/jobs/{job_id:int}/retry", status_code=202)
def retry_job(job_id: int, tasks: BackgroundTasks, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    job = _my_job(db, job_id, user)
    if job.status != "failed":
        raise HTTPException(400, f"Only failed jobs can be retried (this one is {job.status})")
    if job.attempts >= job.max_attempts:
        raise HTTPException(400, f"Job already tried {job.attempts} times, no attempts left")
    tasks.add_task(run_job, job.id)
    return {"job_id": job.id, "status": "retrying", "attempt": job.attempts + 1}


@router.post("/jobs/retry-failed", status_code=202)
def retry_all_failed(tasks: BackgroundTasks, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    """retries every failed job that still has attempts left"""
    jobs = (db.query(BackgroundJob)
            .filter(BackgroundJob.status == "failed", BackgroundJob.attempts < BackgroundJob.max_attempts).all())
    for job in jobs:
        tasks.add_task(run_job, job.id)
    return {"retrying": [j.id for j in jobs]}


@router.post("/jobs/schedules", status_code=201)
def add_schedule(data: ScheduleCreate, user: User = Depends(admin_only)):
    if not scheduler.running:
        scheduler.start()
    scheduler.add_job(_scheduled_run, "interval", minutes=data.every_minutes, args=[data.job_type, user.id],
                      id=data.job_type, replace_existing=True)
    return {"job_type": data.job_type, "every_minutes": data.every_minutes,
            "next_run": scheduler.get_job(data.job_type).next_run_time}


@router.get("/jobs/schedules")
def list_schedules(user: User = Depends(admin_only)):
    return [{"job_type": j.id, "every_minutes": int(j.trigger.interval.total_seconds() // 60),
             "next_run": j.next_run_time} for j in scheduler.get_jobs()]


@router.delete("/jobs/schedules/{job_type}", status_code=204)
def remove_schedule(job_type: str, user: User = Depends(admin_only)):
    if not scheduler.get_job(job_type):
        raise HTTPException(404, "No schedule for this job type")
    scheduler.remove_job(job_type)
