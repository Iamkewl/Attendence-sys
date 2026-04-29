"""Demographic fairness auditing for recognition performance monitoring."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.student import Student


@dataclass
class FairnessMetrics:
    """Recognition metrics for one cohort/group."""

    sample_count: int
    genuine_attempts: int
    impostor_attempts: int
    predicted_positive: int
    true_positive: int
    false_positive: int
    false_non_match: int
    false_match_count: int
    precision: float
    recall: float
    fmr: float
    fnmr: float


class FairnessAuditor:
    """Compute recognition fairness metrics split by demographic cohorts."""

    def __init__(self, *, min_group_samples: int = 5) -> None:
        self.min_group_samples = int(max(min_group_samples, 1))

    @staticmethod
    def _safe_div(num: float, den: float) -> float:
        if den <= 0.0:
            return 0.0
        return float(num / den)

    @classmethod
    def _compute_metrics(cls, rows: list[dict[str, Any]]) -> FairnessMetrics:
        sample_count = len(rows)
        genuine_attempts = 0
        impostor_attempts = 0
        predicted_positive = 0
        true_positive = 0
        false_positive = 0
        false_non_match = 0
        false_match_count = 0

        for row in rows:
            expected = row.get("expected_student_id")
            predicted = row.get("predicted_student_id")

            has_expected = expected is not None
            has_predicted = predicted is not None

            if has_expected:
                genuine_attempts += 1
            else:
                impostor_attempts += 1

            if has_predicted:
                predicted_positive += 1

            if has_expected and has_predicted and int(predicted) == int(expected):
                true_positive += 1
            elif has_expected:
                false_non_match += 1

            if has_predicted:
                if not has_expected:
                    false_positive += 1
                    false_match_count += 1
                elif int(predicted) != int(expected):
                    false_positive += 1
                    false_match_count += 1

        precision = cls._safe_div(float(true_positive), float(true_positive + false_positive))
        recall = cls._safe_div(float(true_positive), float(genuine_attempts))
        fmr = cls._safe_div(float(false_match_count), float(impostor_attempts))
        fnmr = cls._safe_div(float(false_non_match), float(genuine_attempts))

        return FairnessMetrics(
            sample_count=sample_count,
            genuine_attempts=genuine_attempts,
            impostor_attempts=impostor_attempts,
            predicted_positive=predicted_positive,
            true_positive=true_positive,
            false_positive=false_positive,
            false_non_match=false_non_match,
            false_match_count=false_match_count,
            precision=precision,
            recall=recall,
            fmr=fmr,
            fnmr=fnmr,
        )

    @staticmethod
    def _demographics_by_student(db: Session) -> dict[int, dict[str, Any]]:
        rows = db.execute(
            select(Student.id, Student.department, Student.enrollment_year)
        ).all()
        payload: dict[int, dict[str, Any]] = {}
        for sid, department, enrollment_year in rows:
            payload[int(sid)] = {
                "department": (str(department).strip() if department else "unknown"),
                "enrollment_year": int(enrollment_year) if enrollment_year is not None else None,
            }
        return payload

    @staticmethod
    def _normalize_student_id(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            sid = int(value)
            return sid if sid > 0 else None
        except (TypeError, ValueError):
            return None

    def _group_value(
        self,
        row: dict[str, Any],
        *,
        grouping: str,
        demographics: dict[int, dict[str, Any]],
    ) -> str:
        expected_id = self._normalize_student_id(row.get("expected_student_id"))
        student_meta = demographics.get(expected_id or -1, {})

        if grouping == "department":
            return str(student_meta.get("department") or "unknown")
        if grouping == "enrollment_year":
            year = student_meta.get("enrollment_year")
            return str(year) if year is not None else "unknown"
        if grouping == "self_reported_category":
            value = row.get("self_reported_category")
            if value is None:
                return "unspecified"
            text = str(value).strip()
            return text if text else "unspecified"
        return "unknown"

    def build_records(
        self,
        db: Session,
        dataset_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        demographics = self._demographics_by_student(db)
        records: list[dict[str, Any]] = []

        for row in dataset_rows:
            expected_id = self._normalize_student_id(row.get("expected_student_id"))
            predicted_id = self._normalize_student_id(row.get("predicted_student_id"))
            confidence_raw = row.get("confidence")
            try:
                confidence = float(confidence_raw) if confidence_raw is not None else None
            except (TypeError, ValueError):
                confidence = None

            records.append(
                {
                    "sample_id": row.get("sample_id"),
                    "expected_student_id": expected_id,
                    "predicted_student_id": predicted_id,
                    "confidence": confidence,
                    "department": self._group_value(
                        row,
                        grouping="department",
                        demographics=demographics,
                    ),
                    "enrollment_year": self._group_value(
                        row,
                        grouping="enrollment_year",
                        demographics=demographics,
                    ),
                    "self_reported_category": self._group_value(
                        row,
                        grouping="self_reported_category",
                        demographics=demographics,
                    ),
                }
            )
        return records

    def _group_breakdown(
        self,
        records: list[dict[str, Any]],
        *,
        key: str,
    ) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in records:
            group_value = str(row.get(key) or "unknown")
            grouped.setdefault(group_value, []).append(row)

        report: dict[str, dict[str, Any]] = {}
        for group_value, rows in sorted(grouped.items()):
            metrics = self._compute_metrics(rows)
            report[group_value] = {
                "sample_count": metrics.sample_count,
                "genuine_attempts": metrics.genuine_attempts,
                "impostor_attempts": metrics.impostor_attempts,
                "predicted_positive": metrics.predicted_positive,
                "true_positive": metrics.true_positive,
                "false_positive": metrics.false_positive,
                "false_non_match": metrics.false_non_match,
                "false_match_count": metrics.false_match_count,
                "precision": round(metrics.precision, 6),
                "recall": round(metrics.recall, 6),
                "fmr": round(metrics.fmr, 6),
                "fnmr": round(metrics.fnmr, 6),
                "eligible_for_disparity": metrics.sample_count >= self.min_group_samples,
            }
        return report

    @staticmethod
    def _disparity_ratio(group_report: dict[str, dict[str, Any]], metric: str) -> float | None:
        values = [
            float(item.get(metric, 0.0))
            for item in group_report.values()
            if bool(item.get("eligible_for_disparity", False))
        ]
        if len(values) < 2:
            return None

        low = min(values)
        high = max(values)
        if low <= 0.0:
            return None
        return float(high / low)

    def generate_report(
        self,
        records: list[dict[str, Any]],
        *,
        generated_by: str,
    ) -> dict[str, Any]:
        overall = self._compute_metrics(records)

        by_department = self._group_breakdown(records, key="department")
        by_enrollment_year = self._group_breakdown(records, key="enrollment_year")

        has_self_reported = any(
            row.get("self_reported_category") not in {None, "", "unspecified"}
            for row in records
        )
        by_self_reported = (
            self._group_breakdown(records, key="self_reported_category")
            if has_self_reported
            else {}
        )

        disparity = {
            "department": {
                "precision": self._disparity_ratio(by_department, "precision"),
                "recall": self._disparity_ratio(by_department, "recall"),
                "fmr": self._disparity_ratio(by_department, "fmr"),
                "fnmr": self._disparity_ratio(by_department, "fnmr"),
            },
            "enrollment_year": {
                "precision": self._disparity_ratio(by_enrollment_year, "precision"),
                "recall": self._disparity_ratio(by_enrollment_year, "recall"),
                "fmr": self._disparity_ratio(by_enrollment_year, "fmr"),
                "fnmr": self._disparity_ratio(by_enrollment_year, "fnmr"),
            },
            "self_reported_category": {
                "precision": self._disparity_ratio(by_self_reported, "precision"),
                "recall": self._disparity_ratio(by_self_reported, "recall"),
                "fmr": self._disparity_ratio(by_self_reported, "fmr"),
                "fnmr": self._disparity_ratio(by_self_reported, "fnmr"),
            }
            if has_self_reported
            else {},
        }

        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "generated_by": generated_by,
            "min_group_samples": self.min_group_samples,
            "record_count": len(records),
            "overall": {
                "sample_count": overall.sample_count,
                "genuine_attempts": overall.genuine_attempts,
                "impostor_attempts": overall.impostor_attempts,
                "predicted_positive": overall.predicted_positive,
                "true_positive": overall.true_positive,
                "false_positive": overall.false_positive,
                "false_non_match": overall.false_non_match,
                "false_match_count": overall.false_match_count,
                "precision": round(overall.precision, 6),
                "recall": round(overall.recall, 6),
                "fmr": round(overall.fmr, 6),
                "fnmr": round(overall.fnmr, 6),
            },
            "groups": {
                "department": by_department,
                "enrollment_year": by_enrollment_year,
                "self_reported_category": by_self_reported,
            },
            "disparity_ratios": disparity,
        }


def load_fairness_dataset(dataset_path: str | Path) -> list[dict[str, Any]]:
    """Load audit dataset from JSON, JSONL, or CSV."""
    path = Path(dataset_path)
    if not path.exists():
        raise FileNotFoundError(f"Fairness dataset not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        raise ValueError("JSON dataset must be a list of objects")

    if suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            item = json.loads(text)
            if isinstance(item, dict):
                rows.append(item)
        return rows

    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for row in reader]

    raise ValueError("Unsupported fairness dataset format. Use .json, .jsonl, or .csv")


def save_fairness_report(report: dict[str, Any], output_dir: str | Path) -> Path:
    """Persist report to timestamped file and update latest pointer."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / f"fairness_audit_{timestamp}.json"
    latest = out_dir / "fairness_audit_latest.json"

    payload = json.dumps(report, indent=2)
    target.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    return target
