"""Calibration score logger — ported from V1.

Logs all face match scores (positive + negative) to a CSV for
threshold calibration and ROC analysis.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


class CalibrationScoreLogger:
    """Thread-safe CSV logger for face match calibration data."""

    def __init__(
        self, csv_path: str = "backend/data/calibration_scores.csv"
    ) -> None:
        self._path = Path(csv_path)
        self._lock = Lock()
        self._headers = [
            "timestamp_utc",
            "score",
            "label",
            "source",
            "target_student_id",
            "target_student_name",
            "candidate_student_id",
            "candidate_student_name",
        ]
        self._ensure_file()

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_file(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists() and self._path.stat().st_size > 0:
            return
        with self._path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=self._headers)
            writer.writeheader()

    def append_many(self, rows: list[dict]) -> int:
        """Append multiple calibration rows atomically."""
        if not rows:
            return 0

        prepared_rows: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            prepared_rows.append(
                {
                    "timestamp_utc": row.get("timestamp_utc", now),
                    "score": float(row["score"]),
                    "label": int(row["label"]),
                    "source": row.get("source", "unknown"),
                    "target_student_id": row.get("target_student_id", ""),
                    "target_student_name": row.get("target_student_name", ""),
                    "candidate_student_id": row.get("candidate_student_id", ""),
                    "candidate_student_name": row.get(
                        "candidate_student_name", ""
                    ),
                }
            )

        with self._lock:
            self._ensure_file()
            with self._path.open("a", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=self._headers)
                writer.writerows(prepared_rows)

        return len(prepared_rows)

    def reset(self) -> None:
        """Reset the CSV file (header only)."""
        with self._lock:
            with self._path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=self._headers)
                writer.writeheader()

    def stats(self) -> dict:
        """Return summary statistics of logged calibration data."""
        with self._lock:
            self._ensure_file()
            positives = 0
            negatives = 0
            total = 0
            with self._path.open("r", newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    total += 1
                    if int(row.get("label", 0)) == 1:
                        positives += 1
                    else:
                        negatives += 1

        return {
            "rows": total,
            "positives": positives,
            "negatives": negatives,
            "csv_path": str(self._path),
        }


# Module-level singleton
calibration_logger = CalibrationScoreLogger()
