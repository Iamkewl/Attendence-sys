"""Detector benchmark harness for attendance recognition pipeline.

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
import statistics
import sys
import time
from typing import Iterable

import cv2
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.config import get_settings
from backend.services.ai_pipeline import FaceMatch, ai_pipeline


DETECTOR_CHOICES = (
    "yolov8_sahi",
    "yolov12_native",
    "yolov12_sahi",
    "yolo26_reference",
)

RESTORATION_CHOICES = (
    "none",
    "realesrgan",
    "codeformer",
    "both",
)


@dataclass
class EvalSample:
    image_path: Path
    expected_ids: set[int]


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


def _to_jsonable_match(match: FaceMatch) -> dict:
    return {
        "student_id": int(match.student_id),
        "confidence": float(match.confidence),
        "quality": float(match.quality),
        "bbox": [int(v) for v in match.bbox],
    }


def _compute_metrics(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = float(tp / max(tp + fp, 1))
    recall = float(tp / max(tp + fn, 1))
    denom = precision + recall
    f1 = float((2.0 * precision * recall / denom) if denom > 0 else 0.0)
    return precision, recall, f1


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float32), q))


def _restoration_stats_delta(before: dict, after: dict) -> dict:
    keys = {
        "codeformer_attempts",
        "codeformer_applied",
        "codeformer_discarded",
        "codeformer_latency_total_ms",
        "codeformer_latency_samples",
    }
    delta = {key: float(after.get(key, 0.0) - before.get(key, 0.0)) for key in keys}
    attempts = max(delta["codeformer_attempts"], 0.0)
    latency_samples = max(delta["codeformer_latency_samples"], 0.0)
    delta["artifact_discard_rate"] = (
        float(delta["codeformer_discarded"]) / float(attempts)
        if attempts > 0
        else 0.0
    )
    delta["codeformer_latency_mean_ms"] = (
        float(delta["codeformer_latency_total_ms"]) / float(latency_samples)
        if latency_samples > 0
        else 0.0
    )
    return delta


def evaluate_detector(
    *,
    db_session: Session,
    samples: Iterable[EvalSample],
    detector_mode: str,
    restoration_mode: str,
) -> dict:
    latencies_ms: list[float] = []
    margins: list[float] = []
    confidences: list[float] = []

    total_detected_faces = 0
    total_quality_pass_faces = 0
    total_recognized_faces = 0
    tp = 0
    fp = 0
    fn = 0
    sample_rows: list[dict] = []
    runtime_gates = ai_pipeline.get_runtime_gates()
    restoration_before = ai_pipeline.get_restoration_stats(reset=False)

    for sample in samples:
        image_bgr = cv2.imread(str(sample.image_path), cv2.IMREAD_COLOR)
        if image_bgr is None:
            sample_rows.append(
                {
                    "image": str(sample.image_path),
                    "error": "image_load_failed",
                    "expected_student_ids": sorted(sample.expected_ids),
                }
            )
            fn += len(sample.expected_ids)
            continue

        detected_boxes = ai_pipeline.detect_faces(image_bgr, detector_mode=detector_mode)
        total_detected_faces += len(detected_boxes)

        quality_pass_faces = 0
        for x, y, w, h in detected_boxes:
            x1 = max(int(x), 0)
            y1 = max(int(y), 0)
            x2 = min(int(x + w), image_bgr.shape[1])
            y2 = min(int(y + h), image_bgr.shape[0])
            if x2 <= x1 or y2 <= y1:
                continue
            crop = image_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            quality_score, sharpness = ai_pipeline.face_quality_score(
                crop_bgr=crop,
                bbox=(int(x), int(y), int(w), int(h)),
                full_image_shape=image_bgr.shape,
                runtime_gates=runtime_gates,
            )
            if ai_pipeline._is_face_usable(
                (int(x), int(y), int(w), int(h)),
                quality_score=quality_score,
                sharpness=sharpness,
                runtime_gates=runtime_gates,
            ):
                quality_pass_faces += 1

        total_quality_pass_faces += int(quality_pass_faces)

        started = time.perf_counter()
        matches = ai_pipeline.recognize(
            db_session=db_session,
            image_bgr=image_bgr,
            schedule_id=0,
            detector_mode=detector_mode,
            restoration_mode=restoration_mode,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        latencies_ms.append(elapsed_ms)

        total_recognized_faces += len(matches)
        recognized_ids = {int(m.student_id) for m in matches}
        expected_ids = set(sample.expected_ids)

        tp += len(expected_ids & recognized_ids)
        fp += len(recognized_ids - expected_ids)
        fn += len(expected_ids - recognized_ids)

        if matches:
            ordered = sorted(matches, key=lambda m: float(m.confidence), reverse=True)
            confidences.extend(float(item.confidence) for item in ordered)
            if len(ordered) >= 2:
                margins.append(float(ordered[0].confidence - ordered[1].confidence))
            else:
                margins.append(float(ordered[0].confidence))

        sample_rows.append(
            {
                "image": str(sample.image_path),
                "expected_student_ids": sorted(expected_ids),
                "recognized_student_ids": sorted(recognized_ids),
                "detected_faces": len(detected_boxes),
                "quality_pass_faces": int(quality_pass_faces),
                "recognized_faces": len(matches),
                "latency_ms": elapsed_ms,
                "matches": [_to_jsonable_match(match) for match in matches],
            }
        )

    restoration_after = ai_pipeline.get_restoration_stats(reset=False)
    restoration_delta = _restoration_stats_delta(restoration_before, restoration_after)
    precision, recall, f1 = _compute_metrics(tp=tp, fp=fp, fn=fn)
    mean_latency_ms = float(statistics.fmean(latencies_ms)) if latencies_ms else 0.0

    return {
        "detector": detector_mode,
        "restoration_mode": restoration_mode,
        "samples": len(sample_rows),
        "faces_detected": int(total_detected_faces),
        "quality_pass_faces": int(total_quality_pass_faces),
        "recognized_faces": int(total_recognized_faces),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_latency_ms": mean_latency_ms,
        "latency_p50_ms": _percentile(latencies_ms, 50),
        "latency_p95_ms": _percentile(latencies_ms, 95),
        "mean_confidence": float(statistics.fmean(confidences)) if confidences else 0.0,
        "score_margin_distribution": {
            "count": len(margins),
            "p50": _percentile(margins, 50),
            "p90": _percentile(margins, 90),
            "p95": _percentile(margins, 95),
            "min": float(min(margins)) if margins else 0.0,
            "max": float(max(margins)) if margins else 0.0,
        },
        "confusion_counts": {
            "tp": int(tp),
            "fp": int(fp),
            "fn": int(fn),
        },
        "restoration": restoration_delta,
        "sample_rows": sample_rows,
    }


def _build_sync_session_factory():
    settings = get_settings()
    sync_url = settings.database_url.replace("+asyncpg", "")
    engine = create_engine(sync_url, pool_pre_ping=True)
    return sessionmaker(bind=engine), engine


def _build_restoration_comparison(per_mode: dict[str, dict]) -> dict[str, dict]:
    baseline = per_mode.get("none")
    if baseline is None:
        return {}

    baseline_recall = float(baseline.get("recall", 0.0))
    baseline_conf = float(baseline.get("mean_confidence", 0.0))
    baseline_latency = float(baseline.get("mean_latency_ms", 0.0))

    summary: dict[str, dict] = {}
    for mode, payload in per_mode.items():
        mode_recall = float(payload.get("recall", 0.0))
        mode_conf = float(payload.get("mean_confidence", 0.0))
        mode_latency = float(payload.get("mean_latency_ms", 0.0))
        restoration_meta = payload.get("restoration", {})

        summary[mode] = {
            "recognition_rate_gain": mode_recall - baseline_recall,
            "mean_confidence_delta": mode_conf - baseline_conf,
            "artifact_discard_rate": float(restoration_meta.get("artifact_discard_rate", 0.0)),
            "latency_overhead_ms": mode_latency - baseline_latency,
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark detector and restoration backends on labeled samples"
    )
    parser.add_argument("--manifest", required=True, help="CSV manifest path")
    parser.add_argument(
        "--detector",
        action="append",
        choices=DETECTOR_CHOICES,
        help="Detector mode to benchmark (can be passed multiple times)",
    )
    parser.add_argument(
        "--all-detectors",
        action="store_true",
        help="Run all detector modes including yolo26 reference",
    )
    parser.add_argument(
        "--restoration",
        action="append",
        choices=RESTORATION_CHOICES,
        help="Restoration mode to benchmark (can be passed multiple times)",
    )
    parser.add_argument(
        "--all-restorations",
        action="store_true",
        help="Run all restoration modes: none, realesrgan, codeformer, both",
    )
    parser.add_argument(
        "--output-dir",
        default="backend/data/baseline",
        help="Directory for benchmark output JSON",
    )

    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = load_manifest(manifest_path)
    if not samples:
        raise SystemExit("Manifest contains no valid rows")

    if args.all_detectors:
        detectors = list(DETECTOR_CHOICES)
    elif args.detector:
        detectors = args.detector
    else:
        detectors = ["yolov8_sahi"]

    if args.all_restorations:
        restorations = list(RESTORATION_CHOICES)
    elif args.restoration:
        restorations = args.restoration
    else:
        restorations = ["none"]

    session_factory, engine = _build_sync_session_factory()
    started = datetime.now(timezone.utc)

    try:
        ai_pipeline.ensure_loaded()
        matrix: dict[str, dict[str, dict]] = {}
        comparison: dict[str, dict[str, dict]] = {}
        with session_factory() as db_session:
            for detector in detectors:
                per_mode: dict[str, dict] = {}
                for restoration_mode in restorations:
                    per_mode[restoration_mode] = evaluate_detector(
                        db_session=db_session,
                        samples=samples,
                        detector_mode=detector,
                        restoration_mode=restoration_mode,
                    )
                matrix[detector] = per_mode
                comparison[detector] = _build_restoration_comparison(per_mode)

        finished = datetime.now(timezone.utc)
        output_payload = {
            "generated_at": finished.isoformat(),
            "started_at": started.isoformat(),
            "manifest": str(manifest_path),
            "detectors": detectors,
            "restorations": restorations,
            "results": matrix,
            "comparison": comparison,
        }

        timestamp = finished.strftime("%Y%m%dT%H%M%SZ")
        out_path = output_dir / f"detector_benchmark_{timestamp}.json"
        out_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")

        print(f"Wrote benchmark matrix: {out_path}")
        for detector in detectors:
            for restoration_mode in restorations:
                item = matrix[detector][restoration_mode]
                comp = comparison.get(detector, {}).get(restoration_mode, {})
                print(
                    f"{detector}/{restoration_mode}: recall={item['recall']:.4f}, "
                    f"precision={item['precision']:.4f}, f1={item['f1']:.4f}, "
                    f"p50={item['latency_p50_ms']:.2f}ms, p95={item['latency_p95_ms']:.2f}ms, "
                    f"gain={comp.get('recognition_rate_gain', 0.0):+.4f}, "
                    f"conf_delta={comp.get('mean_confidence_delta', 0.0):+.4f}, "
                    f"discard={comp.get('artifact_discard_rate', 0.0):.4f}, "
                    f"latency_overhead={comp.get('latency_overhead_ms', 0.0):+.2f}ms"
                )
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
