"""3-tier liveness verification — ported from V1 (P1-4 / P3-2).

Tiers (cheapest first):
  Tier 1: Frame-difference motion (< 5 ms for 5 frames)
  Tier 2: Farneback optical flow analysis (~20-50 ms)
  Tier 3: MiniFASNet passive anti-spoof CNN via ONNX (~10 ms per frame)

Cascades through tiers — returns LivenessResult with per-tier verdicts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class LivenessResult:
    """Liveness check result with per-tier diagnostics."""

    is_live: bool
    tier1_motion_score: float = 0.0
    tier1_pass: bool = False
    tier2_flow_magnitude: float = 0.0
    tier2_flow_std: float = 0.0
    tier2_pass: bool = False
    tier3_spoof_score: float = -1.0
    tier3_pass: bool = False
    detail: str = ""


# ── Tier 1: Frame-difference motion ────────────────────────────────


def check_liveness_motion(
    frames: list[np.ndarray],
    threshold: float = 2.5,
) -> tuple[bool, float]:
    """Compare consecutive frames for micro-movement.

    Real people exhibit small motions from breathing and micro-expressions.
    A static photo produces near-zero frame difference.
    """
    if len(frames) < 3:
        return False, 0.0

    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    diffs: list[float] = []
    for i in range(1, len(grays)):
        diff = cv2.absdiff(grays[i], grays[i - 1])
        diffs.append(float(np.mean(diff)))

    avg_motion = float(np.mean(diffs))
    return avg_motion > threshold, avg_motion


# ── Tier 2: Optical flow vector field ──────────────────────────────


def check_liveness_optical_flow(
    frames: list[np.ndarray],
    min_magnitude: float = 0.8,
    min_std: float = 0.3,
) -> tuple[bool, float, float]:
    """Farneback dense optical flow — captures natural head sway.

    Returns (is_live, avg_75th_percentile_magnitude, std_magnitude).
    """
    if len(frames) < 3:
        return False, 0.0, 0.0

    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    magnitudes: list[float] = []
    for i in range(1, len(grays)):
        flow = cv2.calcOpticalFlowFarneback(
            grays[i - 1],
            grays[i],
            None,
            pyr_scale=0.5,
            levels=3,
            winsize=15,
            iterations=3,
            poly_n=5,
            poly_sigma=1.2,
            flags=0,
        )
        mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
        magnitudes.append(float(np.percentile(mag, 75)))

    avg_mag = float(np.mean(magnitudes))
    std_mag = float(np.std(magnitudes))
    is_live = avg_mag > min_magnitude and std_mag > min_std
    return is_live, avg_mag, std_mag


# ── Tier 3: MiniFASNet passive anti-spoof CNN ──────────────────────


class LivenessCNNChecker:
    """Passive anti-spoofing with MiniFASNet-style ONNX CNN.

    Expects an ONNX model: 80×80 BGR face crop → [real_prob, spoof_prob].
    """

    def __init__(
        self, model_path: str = "models/anti_spoof_2.7_80x80.onnx"
    ) -> None:
        self._model_path = Path(model_path)
        self._session = None
        self._loaded = False

    def _try_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._model_path.exists():
            logger.info(
                "liveness_cnn: model not found at %s — tier 3 disabled",
                self._model_path,
            )
            return
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(
                str(self._model_path),
                providers=["CPUExecutionProvider"],
            )
            logger.info(
                "liveness_cnn: loaded MiniFASNet from %s", self._model_path
            )
        except Exception as exc:
            logger.warning("liveness_cnn: failed to load: %s", exc)

    @property
    def available(self) -> bool:
        self._try_load()
        return self._session is not None

    def check_single_frame(self, face_crop_bgr: np.ndarray) -> float:
        """Return spoof probability (0-1). Lower = more likely real."""
        self._try_load()
        if self._session is None:
            return 0.0

        inp = cv2.resize(face_crop_bgr, (80, 80))
        inp = inp.astype(np.float32) / 255.0
        inp = np.transpose(inp, (2, 0, 1))[np.newaxis]

        input_name = self._session.get_inputs()[0].name
        result = self._session.run(None, {input_name: inp})
        probs = result[0][0]
        return float(probs[1]) if len(probs) >= 2 else 0.0

    def check_clip(
        self,
        face_crops: list[np.ndarray],
        spoof_threshold: float = 0.5,
    ) -> tuple[bool, float]:
        """Vote across multiple frames. Returns (is_live, avg_spoof_score)."""
        if not face_crops:
            return False, 1.0

        scores = [self.check_single_frame(c) for c in face_crops]
        avg_score = float(np.mean(scores))
        real_votes = sum(1 for s in scores if s < spoof_threshold)
        is_live = real_votes >= len(scores) * 0.6
        return is_live, avg_score


# Module-level singleton
liveness_cnn = LivenessCNNChecker()


# ── Unified orchestrator ───────────────────────────────────────────


def check_liveness(
    frames: list[np.ndarray],
    face_crops: list[np.ndarray] | None = None,
    *,
    motion_threshold: float = 2.5,
    flow_min_magnitude: float = 0.8,
    spoof_threshold: float = 0.5,
) -> LivenessResult:
    """Run all liveness tiers in cascade — cheapest first.

    Args:
        frames: Full-frame images sampled from the 5s clip.
        face_crops: Face crops for CNN anti-spoof (Tier 3). Optional.
    """
    result = LivenessResult(is_live=False)

    # Tier 1: Frame-difference motion
    t1_pass, t1_score = check_liveness_motion(frames, threshold=motion_threshold)
    result.tier1_pass = t1_pass
    result.tier1_motion_score = t1_score

    if not t1_pass:
        result.detail = (
            "Tier 1 failed: no motion detected (possible static image spoof)"
        )
        return result

    # Tier 2: Optical flow
    t2_pass, t2_mag, t2_std = check_liveness_optical_flow(
        frames, min_magnitude=flow_min_magnitude
    )
    result.tier2_pass = t2_pass
    result.tier2_flow_magnitude = t2_mag
    result.tier2_flow_std = t2_std

    if not t2_pass:
        if t1_score < motion_threshold * 2.0:
            result.detail = (
                "Tier 2 failed: optical flow too uniform (possible video replay)"
            )
            return result
        result.tier2_pass = True

    # Tier 3: CNN anti-spoof
    if face_crops and liveness_cnn.available:
        t3_pass, t3_score = liveness_cnn.check_clip(
            face_crops, spoof_threshold=spoof_threshold
        )
        result.tier3_pass = t3_pass
        result.tier3_spoof_score = t3_score
        if not t3_pass:
            result.detail = (
                "Tier 3 failed: CNN anti-spoof detected presentation attack"
            )
            return result

    result.is_live = True
    result.detail = "all tiers passed"
    return result
