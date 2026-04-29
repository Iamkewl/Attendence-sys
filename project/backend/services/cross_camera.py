"""Cross-camera ReID linker for temporal identity continuity."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from backend.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class _Candidate:
    camera_id: str
    track_id: int
    student_id: int
    person_embedding: np.ndarray
    timestamp: float


@dataclass
class _LinkMetrics:
    link_count: int = 0
    rejected_link_count: int = 0
    confidence_samples: list[float] = field(default_factory=list)


class CrossCameraLinker:
    """Maintains global cross-camera candidates and links unresolved tracks."""

    def __init__(self) -> None:
        self._candidates: list[_Candidate] = []
        self._metrics = _LinkMetrics()
        self._priors = self._load_transition_priors()

    @staticmethod
    def _pair_key(camera_a: str, camera_b: str) -> str:
        return f"{camera_a}->{camera_b}"

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom <= 1e-8:
            return 0.0
        return float(np.dot(a, b) / denom)

    def _load_transition_priors(self) -> dict:
        priors_path = Path(settings.camera_transition_priors_path)
        if not priors_path.exists():
            return {}

        try:
            payload = json.loads(priors_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {}
            return payload
        except Exception as exc:
            logger.warning("cross_camera: failed to parse transition priors: %s", exc)
            return {}

    def _prior_for(self, src_camera: str, dst_camera: str) -> tuple[float, float]:
        default_expected = 60.0
        default_tolerance = 45.0

        default_block = self._priors.get("default") if isinstance(self._priors, dict) else None
        if isinstance(default_block, dict):
            default_expected = float(default_block.get("expected_seconds", default_expected))
            default_tolerance = float(default_block.get("tolerance_seconds", default_tolerance))

        transitions = self._priors.get("transitions") if isinstance(self._priors, dict) else None
        if isinstance(transitions, dict):
            key = self._pair_key(src_camera, dst_camera)
            block = transitions.get(key)
            if isinstance(block, dict):
                expected = float(block.get("expected_seconds", default_expected))
                tolerance = float(block.get("tolerance_seconds", default_tolerance))
                return expected, max(1.0, tolerance)

        return default_expected, max(1.0, default_tolerance)

    def _temporal_score(self, src_camera: str, dst_camera: str, delta_seconds: float) -> float:
        expected, tolerance = self._prior_for(src_camera, dst_camera)
        diff = abs(float(delta_seconds) - expected)
        if diff >= tolerance:
            return 0.0
        return float(1.0 - (diff / tolerance))

    def _prune_old(self, now: float) -> None:
        window = float(max(10, settings.cross_camera_time_window_seconds))
        cutoff = now - window
        self._candidates = [candidate for candidate in self._candidates if candidate.timestamp >= cutoff]

    def register_confirmed_track(self, camera_id: str, track) -> None:
        """Add/refresh one confirmed identity candidate for global linking."""
        if track.identity is None or track.best_person_embedding is None:
            return

        now = time.time()
        self._prune_old(now)

        self._candidates = [
            c
            for c in self._candidates
            if not (c.camera_id == str(camera_id) and c.track_id == int(track.track_id))
        ]
        self._candidates.append(
            _Candidate(
                camera_id=str(camera_id),
                track_id=int(track.track_id),
                student_id=int(track.identity),
                person_embedding=np.asarray(track.best_person_embedding, dtype=np.float32),
                timestamp=now,
            )
        )

    def try_link_track(self, camera_id: str, track) -> dict | None:
        """Try linking unresolved track with candidates from other cameras."""
        if track.best_person_embedding is None:
            self._metrics.rejected_link_count += 1
            return None

        now = time.time()
        self._prune_old(now)

        candidates = [
            candidate
            for candidate in self._candidates
            if candidate.camera_id != str(camera_id)
        ]
        if not candidates:
            self._metrics.rejected_link_count += 1
            return None

        face_weight = float(settings.cross_camera_face_weight)
        reid_weight = float(settings.cross_camera_reid_weight)
        time_weight = float(settings.cross_camera_time_weight)
        threshold = float(settings.cross_camera_link_threshold)

        best_payload: dict | None = None
        best_score = -1.0

        query = np.asarray(track.best_person_embedding, dtype=np.float32)
        for candidate in candidates:
            reid_similarity = self._cosine(query, candidate.person_embedding)
            delta_seconds = max(0.0, now - float(candidate.timestamp))
            temporal_score = self._temporal_score(
                candidate.camera_id,
                str(camera_id),
                delta_seconds,
            )

            face_similarity = 0.0
            if track.identity is not None and int(track.identity) == candidate.student_id:
                face_similarity = max(float(track.confidence), 0.0)

            fused = (
                (face_weight * face_similarity)
                + (reid_weight * reid_similarity)
                + (time_weight * temporal_score)
            )

            if fused > best_score:
                best_score = fused
                best_payload = {
                    "student_id": int(candidate.student_id),
                    "source_camera_id": str(candidate.camera_id),
                    "source_track_id": int(candidate.track_id),
                    "reid_similarity": float(reid_similarity),
                    "temporal_score": float(temporal_score),
                    "fused_score": float(fused),
                }

        if best_payload is None or best_score < threshold:
            self._metrics.rejected_link_count += 1
            return None

        self._metrics.link_count += 1
        self._metrics.confidence_samples.append(float(best_payload["fused_score"]))
        if len(self._metrics.confidence_samples) > 1000:
            self._metrics.confidence_samples = self._metrics.confidence_samples[-1000:]
        return best_payload

    def metrics_snapshot(self) -> dict:
        """Return diagnostics for accepted/rejected links."""
        samples = list(self._metrics.confidence_samples)
        bins = {
            "0.0-0.4": 0,
            "0.4-0.6": 0,
            "0.6-0.8": 0,
            "0.8-1.0": 0,
        }
        for value in samples:
            if value < 0.4:
                bins["0.0-0.4"] += 1
            elif value < 0.6:
                bins["0.4-0.6"] += 1
            elif value < 0.8:
                bins["0.6-0.8"] += 1
            else:
                bins["0.8-1.0"] += 1

        return {
            "link_count": int(self._metrics.link_count),
            "rejected_link_count": int(self._metrics.rejected_link_count),
            "confidence_distribution": bins,
        }


cross_camera_linker = CrossCameraLinker()
