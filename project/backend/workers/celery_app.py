"""Celery application configuration.

Celery workers handle CPU-intensive CV tasks (face detection,
embedding extraction, liveness checks) in separate processes,
each with their own GIL — enabling true parallelism.

Scale via: docker compose up --scale cv-worker=N
"""

from celery import Celery
from celery.schedules import crontab

from backend.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "attendance_v2",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "backend.workers.cv_tasks",
        "backend.workers.governance_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "backend.workers.cv_tasks.*": {"queue": "cv_default"},
        "backend.workers.governance_tasks.*": {"queue": "cv_default"},
    },
    beat_schedule={
        "phase8-monthly-fairness-audit": {
            "task": "backend.workers.governance_tasks.run_monthly_fairness_audit",
            "schedule": crontab(day_of_month=str(settings.fairness_audit_day_of_month), hour=3, minute=0),
        },
        "phase8-nightly-retention-enforcement": {
            "task": "backend.workers.governance_tasks.run_nightly_retention_enforcement",
            "schedule": crontab(hour=int(settings.retention_nightly_hour_utc), minute=0),
        },
        "phase8-nightly-camera-drift-detection": {
            "task": "backend.workers.governance_tasks.run_camera_drift_detection",
            "schedule": crontab(hour=4, minute=0),
        },
    },
)

# Keep autodiscovery for future `tasks.py` modules while `include` guarantees
# current worker task modules are imported when using `-A backend.workers.celery_app`.
celery_app.autodiscover_tasks(["backend.workers"])
