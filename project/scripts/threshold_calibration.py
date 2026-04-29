"""Sweep per-model match thresholds and select operating points for target FMR/FNMR.

Manifest format (CSV):
    image_path,expected_student_ids
    samples/frame_0001.jpg,12
    samples/frame_0002.jpg,3|9

Alternative single-label column is also supported:
    image_path,expected_student_id
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Iterable

import cv2
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.constants import EMBEDDING_DIMENSION
from backend.core.config import get_settings
from backend.services.ai_pipeline import ai_pipeline


MODEL_NAMES = ("arcface", "adaface", "lvface")


@dataclass
class EvalSample:
    image_path: Path
    expected_ids: set[int]


@dataclass
class ProbeEvent:
    expected_ids: set[int]
    predicted_id: int | None
    score: float


def _parse_expected_ids(row: dict[str, str]) -> set[int]:
    if row.get("expected_student_ids"):
        raw_ids = [part.strip() for part in row["expected_student_ids"].split("|")]
    elif row.get("expected_student_id"):
        raw_ids = [row["expected_student_id"].strip()]
    else:
        return set()

    parsed: set[int] = set()
    for token in raw_ids:
        if not token:
            continue
        parsed.add(int(token))
    return parsed


def load_manifest(manifest_path: Path) -> list[EvalSample]:
    samples: list[EvalSample] = []
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("Manifest is missing CSV header")
        if "image_path" not in reader.fieldnames:
            raise ValueError("Manifest must include image_path column")

        for row in reader:
            rel = (row.get("image_path") or "").strip()
            if not rel:
                continue
            image_path = Path(rel)
            if not image_path.is_absolute():
                image_path = (manifest_path.parent / image_path).resolve()
            expected_ids = _parse_expected_ids(row)
            samples.append(EvalSample(image_path=image_path, expected_ids=expected_ids))
    return samples


def _build_sync_session_factory():
    settings = get_settings()
    sync_url = settings.database_url.replace("+asyncpg", "")
    engine = create_engine(sync_url, pool_pre_ping=True)
    return sessionmaker(bind=engine), engine


def _extract_probe(model_name: str, crop_bgr: np.ndarray) -> np.ndarray | None:
    if model_name == "arcface":
        probe = ai_pipeline.extract_embedding(crop_bgr)
    elif model_name == "adaface":
        probe = ai_pipeline.extract_embedding_adaface(crop_bgr)
    else:
        probe = ai_pipeline.extract_embedding_lvface(crop_bgr)

    if probe is None:
        return None

    arr = np.asarray(probe, dtype=np.float32).flatten()
    if arr.shape[0] != EMBEDDING_DIMENSION or not np.isfinite(arr).all():
        return None
    arr /= max(float(np.linalg.norm(arr)), 1e-8)
    return arr


def _top_prediction(
    *,
    probe: np.ndarray,
    matrix: np.ndarray,
    student_rows: dict[int, list[int]],
    retention_scores: np.ndarray,
) -> tuple[int | None, float]:
    per_student = ai_pipeline._score_per_student(
        probe=probe,
        matrix=matrix,
        student_rows=student_rows,
        retention_scores=retention_scores,
    )
    if not per_student:
        return None, -1.0

    sid, score = max(per_student.items(), key=lambda item: item[1])
    return int(sid), float(score)


def collect_events(
    *,
    db_session: Session,
    samples: Iterable[EvalSample],
) -> dict[str, list[ProbeEvent]]:
    runtime_gates = ai_pipeline.get_runtime_gates()

    template_data: dict[str, tuple[np.ndarray, dict[int, list[int]], np.ndarray]] = {}
    for model_name in MODEL_NAMES:
        _, matrix, student_rows, retention = ai_pipeline._build_template_matrix(
            db_session,
            model_name=model_name,
        )
        template_data[model_name] = (matrix, student_rows, retention)

    events: dict[str, list[ProbeEvent]] = {model_name: [] for model_name in MODEL_NAMES}

    for sample in samples:
        image_bgr = cv2.imread(str(sample.image_path), cv2.IMREAD_COLOR)
        if image_bgr is None:
            continue

        boxes = ai_pipeline.detect_faces(image_bgr)
        if not boxes:
            for model_name in MODEL_NAMES:
                events[model_name].append(
                    ProbeEvent(expected_ids=set(sample.expected_ids), predicted_id=None, score=-1.0)
                )
            continue

        # Use the largest usable face as calibration probe per frame.
        boxes = sorted(boxes, key=lambda b: int(b[2] * b[3]), reverse=True)
        selected_crop = None
        for x, y, w, h in boxes:
            x1 = max(x, 0)
            y1 = max(y, 0)
            x2 = min(x + w, image_bgr.shape[1])
            y2 = min(y + h, image_bgr.shape[0])
            if x2 <= x1 or y2 <= y1:
                continue

            crop = image_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            quality_score, sharpness = ai_pipeline.face_quality_score(
                crop_bgr=crop,
                bbox=(x, y, w, h),
                full_image_shape=image_bgr.shape,
                runtime_gates=runtime_gates,
            )
            if not ai_pipeline._is_face_usable(
                (x, y, w, h),
                quality_score=quality_score,
                sharpness=sharpness,
                runtime_gates=runtime_gates,
            ):
                continue

            selected_crop = crop
            break

        if selected_crop is None:
            for model_name in MODEL_NAMES:
                events[model_name].append(
                    ProbeEvent(expected_ids=set(sample.expected_ids), predicted_id=None, score=-1.0)
                )
            continue

        for model_name in MODEL_NAMES:
            matrix, student_rows, retention_scores = template_data[model_name]
            if matrix.shape[0] == 0:
                events[model_name].append(
                    ProbeEvent(expected_ids=set(sample.expected_ids), predicted_id=None, score=-1.0)
                )
                continue

            probe = _extract_probe(model_name, selected_crop)
            if probe is None:
                events[model_name].append(
                    ProbeEvent(expected_ids=set(sample.expected_ids), predicted_id=None, score=-1.0)
                )
                continue

            predicted_id, score = _top_prediction(
                probe=probe,
                matrix=matrix,
                student_rows=student_rows,
                retention_scores=retention_scores,
            )
            events[model_name].append(
                ProbeEvent(
                    expected_ids=set(sample.expected_ids),
                    predicted_id=predicted_id,
                    score=float(score),
                )
            )

    return events


def _evaluate_threshold(events: Iterable[ProbeEvent], threshold: float) -> dict:
    genuine_trials = 0
    impostor_trials = 0
    false_non_matches = 0
    false_matches = 0
    tp = 0
    fp = 0
    fn = 0

    for event in events:
        accepted = event.predicted_id is not None and event.score >= threshold
        expected = event.expected_ids

        if expected:
            genuine_trials += 1
            if accepted and event.predicted_id in expected:
                tp += 1
            else:
                fn += 1
                false_non_matches += 1

            if event.predicted_id is not None and event.predicted_id not in expected:
                impostor_trials += 1
                if accepted:
                    fp += 1
                    false_matches += 1
        else:
            impostor_trials += 1
            if accepted:
                fp += 1
                false_matches += 1

    precision = float(tp / max(tp + fp, 1))
    recall = float(tp / max(tp + fn, 1))
    denom = precision + recall
    f1 = float((2.0 * precision * recall / denom) if denom > 0 else 0.0)

    return {
        "threshold": float(threshold),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_match_rate": float(false_matches / max(impostor_trials, 1)),
        "false_non_match_rate": float(false_non_matches / max(genuine_trials, 1)),
        "genuine_trials": int(genuine_trials),
        "impostor_trials": int(impostor_trials),
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
    }


def calibrate_thresholds(
    *,
    model_events: dict[str, list[ProbeEvent]],
    target_fmr: float,
    target_fnmr: float,
    threshold_min: float,
    threshold_max: float,
    threshold_step: float,
) -> dict:
    calibration: dict[str, dict] = {}

    for model_name, events in model_events.items():
        if not events:
            calibration[model_name] = {
                "best_threshold": None,
                "best_relaxed_threshold": None,
                "notes": "No calibration events available",
                "curve": [],
            }
            continue

        thresholds = np.arange(threshold_min, threshold_max + 1e-8, threshold_step)
        curve = [_evaluate_threshold(events, float(th)) for th in thresholds]

        best = min(
            curve,
            key=lambda row: (
                abs(row["false_match_rate"] - target_fmr)
                + abs(row["false_non_match_rate"] - target_fnmr),
                -row["f1"],
            ),
        )

        relaxed_candidates = [
            row for row in curve if row["threshold"] <= best["threshold"] and row["false_match_rate"] <= (target_fmr * 1.2)
        ]
        if relaxed_candidates:
            relaxed = max(relaxed_candidates, key=lambda row: row["f1"])
            best_relaxed_threshold = float(relaxed["threshold"])
        else:
            best_relaxed_threshold = float(best["threshold"])

        calibration[model_name] = {
            "best_threshold": float(best["threshold"]),
            "best_relaxed_threshold": best_relaxed_threshold,
            "target_fmr": float(target_fmr),
            "target_fnmr": float(target_fnmr),
            "best_metrics": best,
            "curve": curve,
        }

    return calibration


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate ArcFace/AdaFace/LVFace thresholds for target FMR/FNMR"
    )
    parser.add_argument("--manifest", required=True, help="CSV manifest path")
    parser.add_argument(
        "--target-fmr",
        type=float,
        default=0.01,
        help="Target false match rate",
    )
    parser.add_argument(
        "--target-fnmr",
        type=float,
        default=0.05,
        help="Target false non-match rate",
    )
    parser.add_argument("--threshold-min", type=float, default=0.50)
    parser.add_argument("--threshold-max", type=float, default=0.99)
    parser.add_argument("--threshold-step", type=float, default=0.01)
    parser.add_argument(
        "--output-dir",
        default="backend/data/baseline",
        help="Directory for calibration JSON output",
    )

    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = load_manifest(manifest_path)
    if not samples:
        raise SystemExit("Manifest contains no valid rows")

    session_factory, engine = _build_sync_session_factory()
    started = datetime.now(timezone.utc)

    try:
        ai_pipeline.ensure_loaded()
        with session_factory() as db_session:
            events = collect_events(db_session=db_session, samples=samples)

        calibration = calibrate_thresholds(
            model_events=events,
            target_fmr=float(args.target_fmr),
            target_fnmr=float(args.target_fnmr),
            threshold_min=float(args.threshold_min),
            threshold_max=float(args.threshold_max),
            threshold_step=float(args.threshold_step),
        )

        finished = datetime.now(timezone.utc)
        payload = {
            "generated_at": finished.isoformat(),
            "started_at": started.isoformat(),
            "manifest": str(manifest_path),
            "targets": {
                "fmr": float(args.target_fmr),
                "fnmr": float(args.target_fnmr),
            },
            "threshold_range": {
                "min": float(args.threshold_min),
                "max": float(args.threshold_max),
                "step": float(args.threshold_step),
            },
            "models": list(MODEL_NAMES),
            "calibration": calibration,
        }

        timestamp = finished.strftime("%Y%m%dT%H%M%SZ")
        out_path = output_dir / f"threshold_calibration_{timestamp}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        print(f"Wrote threshold calibration report: {out_path}")
        for model_name in MODEL_NAMES:
            best = calibration[model_name].get("best_metrics")
            if not best:
                print(f"{model_name}: no calibration data")
                continue
            print(
                f"{model_name}: best={best['threshold']:.2f}, "
                f"relaxed={calibration[model_name]['best_relaxed_threshold']:.2f}, "
                f"fmr={best['false_match_rate']:.4f}, fnmr={best['false_non_match_rate']:.4f}, f1={best['f1']:.4f}"
            )
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
