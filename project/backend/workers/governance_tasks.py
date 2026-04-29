"""Celery governance tasks: fairness audits, retention, and drift detection."""

from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.config import get_settings
from backend.models.audit import AuditLog
from backend.services.data_retention import enforce_retention_sync
from backend.services.drift_detector import evaluate_camera_drift_sync
from backend.services.fairness_audit import (
    FairnessAuditor,
    load_fairness_dataset,
    save_fairness_report,
)
from backend.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()

_sync_url = settings.database_url.replace("+asyncpg", "")
_sync_engine = create_engine(_sync_url, pool_size=5, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_sync_engine)


@celery_app.task(name="backend.workers.governance_tasks.run_monthly_fairness_audit")
def run_monthly_fairness_audit(dataset_path: str | None = None) -> dict:
    """Run fairness audit against labeled evaluation data."""
    if not settings.enable_fairness_audit:
        return {"status": "skipped", "reason": "enable_fairness_audit=false"}

    audit_path = dataset_path or settings.fairness_audit_dataset_path
    dataset_rows = load_fairness_dataset(audit_path)

    with SyncSession() as db:
        auditor = FairnessAuditor(min_group_samples=settings.fairness_min_group_samples)
        records = auditor.build_records(db, dataset_rows)
        report = auditor.generate_report(records, generated_by="celery:monthly")
        output_path = save_fairness_report(report, settings.fairness_audit_output_dir)

        db.add(
            AuditLog(
                user_id=None,
                action="fairness.audit.monthly",
                resource="compliance",
                details={
                    "dataset_path": str(audit_path),
                    "record_count": len(records),
                    "output_path": str(output_path),
                    "disparity_ratios": report.get("disparity_ratios", {}),
                },
            )
        )
        db.commit()

    logger.info("governance: monthly fairness audit complete output=%s", output_path)
    return {
        "status": "completed",
        "dataset_path": str(audit_path),
        "record_count": len(records),
        "output_path": str(output_path),
    }


@celery_app.task(name="backend.workers.governance_tasks.run_nightly_retention_enforcement")
def run_nightly_retention_enforcement() -> dict:
    """Run nightly data retention policy enforcement."""
    with SyncSession() as db:
        summary = enforce_retention_sync(db)
        db.commit()
    logger.info("governance: retention task result=%s", summary.get("status"))
    return summary


@celery_app.task(name="backend.workers.governance_tasks.run_camera_drift_detection")
def run_camera_drift_detection() -> dict:
    """Run rolling camera-domain drift evaluation and emit alerts."""
    with SyncSession() as db:
        summary = evaluate_camera_drift_sync(db)
        db.commit()
    logger.info("governance: drift scan alerts=%s", summary.get("alert_count", 0))
    return summary
