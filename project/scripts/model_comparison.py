"""Compare ArcFace, AdaFace, and LVFace recognition quality side-by-side.

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


def _score_one_model(
    *,
    model_name: str,
    crop_bgr: np.ndarray,
    matrix: np.ndarray,
    student_rows: dict[int, list[int]],
    retention_scores: np.ndarray,
) -> tuple[int | None, float, float]:
    if model_name == "arcface":
        probe = ai_pipeline.extract_embedding(crop_bgr)
    elif model_name == "adaface":
        probe = ai_pipeline.extract_embedding_adaface(crop_bgr)
    else:
        probe = ai_pipeline.extract_embedding_lvface(crop_bgr)

    if probe is None:
        return None, -1.0, -1.0

    probe = np.asarray(probe, dtype=np.float32).flatten()
    if probe.shape[0] != EMBEDDING_DIMENSION or not np.isfinite(probe).all():
        return None, -1.0, -1.0

    probe /= max(float(np.linalg.norm(probe)), 1e-8)
    per_student = ai_pipeline._score_per_student(
        probe=probe,
        matrix=matrix,
        student_rows=student_rows,
        retention_scores=retention_scores,
    )
    if not per_student:
        return None, -1.0, -1.0

    ordered = sorted(per_student.items(), key=lambda item: item[1], reverse=True)
    best_sid, best_score = ordered[0]
    second_best = float(ordered[1][1]) if len(ordered) > 1 else -1.0
    return int(best_sid), float(best_score), second_best


def _compute_metrics(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = float(tp / max(tp + fp, 1))
    recall = float(tp / max(tp + fn, 1))
    denom = precision + recall
    f1 = float((2.0 * precision * recall / denom) if denom > 0 else 0.0)
    return precision, recall, f1


def evaluate_models(
    *,
    db_session: Session,
    samples: Iterable[EvalSample],
) -> dict:
    runtime_gates = ai_pipeline.get_runtime_gates()

    template_data: dict[str, tuple[np.ndarray, dict[int, list[int]], np.ndarray]] = {}
    for model_name in MODEL_NAMES:
        _, matrix, student_rows, retention = ai_pipeline._build_template_matrix(
            db_session,
            model_name=model_name,
        )
        template_data[model_name] = (matrix, student_rows, retention)

    per_model: dict[str, dict] = {
        model_name: {
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "correct_accepts": 0,
            "false_matches": 0,
            "false_non_matches": 0,
            "accept_events": 0,
            "genuine_events": 0,
            "confidences": [],
            "rows": [],
        }
        for model_name in MODEL_NAMES
    }

    for sample in samples:
        image_bgr = cv2.imread(str(sample.image_path), cv2.IMREAD_COLOR)
        if image_bgr is None:
            for model_name in MODEL_NAMES:
                per_model[model_name]["rows"].append(
                    {
                        "image": str(sample.image_path),
                        "expected_student_ids": sorted(sample.expected_ids),
                        "recognized_student_ids": [],
                        "error": "image_load_failed",
                    }
                )
            continue

        boxes = ai_pipeline.detect_faces(image_bgr)
        predictions: dict[str, set[int]] = {model: set() for model in MODEL_NAMES}
        confidences: dict[str, list[float]] = {model: [] for model in MODEL_NAMES}

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

            for model_name in MODEL_NAMES:
                matrix, student_rows, retention = template_data[model_name]
                if matrix.shape[0] == 0:
                    continue

                best_sid, best_score, second_best = _score_one_model(
                    model_name=model_name,
                    crop_bgr=crop,
                    matrix=matrix,
                    student_rows=student_rows,
                    retention_scores=retention,
                )
                if best_sid is None:
                    continue

                if ai_pipeline._match_decision(
                    best_score,
                    second_best,
                    decision_model=model_name,
                    runtime_gates=runtime_gates,
                ):
                    predictions[model_name].add(best_sid)
                    confidences[model_name].append(float(best_score))

        expected_ids = set(sample.expected_ids)
        for model_name in MODEL_NAMES:
            recognized_ids = predictions[model_name]
            tp = len(expected_ids & recognized_ids)
            fp = len(recognized_ids - expected_ids)
            fn = len(expected_ids - recognized_ids)

            state = per_model[model_name]
            state["tp"] += tp
            state["fp"] += fp
            state["fn"] += fn
            state["correct_accepts"] += tp
            state["false_matches"] += fp
            state["false_non_matches"] += fn
            state["accept_events"] += len(recognized_ids)
            state["genuine_events"] += len(expected_ids)
            state["confidences"].extend(confidences[model_name])
            state["rows"].append(
                {
                    "image": str(sample.image_path),
                    "expected_student_ids": sorted(expected_ids),
                    "recognized_student_ids": sorted(recognized_ids),
                    "recognized_faces": len(recognized_ids),
                }
            )

    output: dict[str, dict] = {}
    for model_name, state in per_model.items():
        tp = int(state["tp"])
        fp = int(state["fp"])
        fn = int(state["fn"])
        precision, recall, f1 = _compute_metrics(tp=tp, fp=fp, fn=fn)

        accept_events = int(state["accept_events"])
        genuine_events = int(state["genuine_events"])
        false_match_rate = float(state["false_matches"] / max(accept_events, 1))
        false_non_match_rate = float(state["false_non_matches"] / max(genuine_events, 1))
        confidences = [float(x) for x in state["confidences"]]

        output[model_name] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "mean_confidence": float(statistics.fmean(confidences)) if confidences else 0.0,
            "false_match_rate": false_match_rate,
            "false_non_match_rate": false_non_match_rate,
            "confusion_counts": {
                "tp": tp,
                "fp": fp,
                "fn": fn,
            },
            "accept_events": accept_events,
            "genuine_events": genuine_events,
            "sample_rows": state["rows"],
        }

    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run side-by-side ArcFace/AdaFace/LVFace model comparison"
    )
    parser.add_argument("--manifest", required=True, help="CSV manifest path")
    parser.add_argument(
        "--output-dir",
        default="backend/data/baseline",
        help="Directory for comparison JSON output",
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
            results = evaluate_models(db_session=db_session, samples=samples)

        finished = datetime.now(timezone.utc)
        payload = {
            "generated_at": finished.isoformat(),
            "started_at": started.isoformat(),
            "manifest": str(manifest_path),
            "models": list(MODEL_NAMES),
            "results": results,
        }

        timestamp = finished.strftime("%Y%m%dT%H%M%SZ")
        out_path = output_dir / f"model_comparison_{timestamp}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        print(f"Wrote comparison report: {out_path}")
        for model_name in MODEL_NAMES:
            item = results[model_name]
            print(
                f"{model_name}: recall={item['recall']:.4f}, "
                f"precision={item['precision']:.4f}, f1={item['f1']:.4f}, "
                f"fmr={item['false_match_rate']:.4f}, fnmr={item['false_non_match_rate']:.4f}"
            )
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
