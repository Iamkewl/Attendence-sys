"""Celery application configuration.

Celery workers handle CPU-intensive CV tasks (face detection,
embedding extraction, liveness checks) in separate processes,
each with their own GIL — enabling true parallelism.

Scale via: docker compose up --scale cv-worker=N
"""

from celery import Celery

from backend.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "attendance_v2",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
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
    },
)

celery_app.autodiscover_tasks(["backend.workers"])
