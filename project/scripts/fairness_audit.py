"""CLI tool for Phase 8 demographic fairness auditing.

Usage:
  python -m scripts.fairness_audit --dataset backend/data/baseline/fairness_dataset.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.config import get_settings
from backend.services.fairness_audit import (
    FairnessAuditor,
    load_fairness_dataset,
    save_fairness_report,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fairness audit over labeled recognition dataset")
    parser.add_argument(
        "--dataset",
        required=True,
        help="Path to fairness dataset (.json, .jsonl, or .csv)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory for report JSON",
    )
    parser.add_argument(
        "--min-group-samples",
        type=int,
        default=None,
        help="Minimum samples required before disparity ratio is computed",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    settings = get_settings()

    sync_url = settings.database_url.replace("+asyncpg", "")
    engine = create_engine(sync_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)

    dataset_rows = load_fairness_dataset(args.dataset)

    with SessionLocal() as db:
        auditor = FairnessAuditor(
            min_group_samples=(
                int(args.min_group_samples)
                if args.min_group_samples is not None
                else int(settings.fairness_min_group_samples)
            )
        )
        records = auditor.build_records(db, dataset_rows)
        report = auditor.generate_report(records, generated_by="cli")

    output_dir = args.output_dir or settings.fairness_audit_output_dir
    output_path = save_fairness_report(report, output_dir)

    summary = {
        "output_path": str(output_path),
        "record_count": len(records),
        "overall": report.get("overall", {}),
        "disparity_ratios": report.get("disparity_ratios", {}),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
