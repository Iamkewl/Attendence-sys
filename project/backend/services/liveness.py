"""5-tier liveness verification.

Tiers (cheapest first):
    Tier 1: Frame-difference motion
    Tier 2: Farneback optical flow
    Tier 3: MiniFASNet passive anti-spoof CNN
    Tier 4: rPPG physiological signal extraction (flag-gated)
    Tier 5: Flash subsurface scattering analysis (flag + camera capability gated)

Depth-style anti-replay is exposed as an optional extension hook and remains
disabled by default until suitable camera hardware is available.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from backend.core.config import build_onnx_execution_providers, get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


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
    tier4_status: str = "skipped"
    tier4_rppg_signal_quality: float = 0.0
    tier4_estimated_hr_bpm: float = 0.0
    tier4_pass: bool = False
    tier5_status: str = "skipped"
    tier5_scattering_score: float = 0.0
    tier5_pattern_analysis: str = ""
    tier5_pass: bool = False
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
        self,
        model_path: str = settings.liveness_anti_spoof_model_path,
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
            provider_chain = build_onnx_execution_providers(
                settings.insightface_provider
            )

            self._session = ort.InferenceSession(
                str(self._model_path),
                providers=provider_chain,
            )
            logger.info(
                "liveness_cnn: loaded MiniFASNet from %s (providers=%s)",
                self._model_path,
                self._session.get_providers(),
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


def _clip_bbox_to_frame(
    bbox: tuple[int, int, int, int],
    frame_shape: tuple[int, int, int],
) -> tuple[int, int, int, int] | None:
    h, w = frame_shape[:2]
    x, y, bw, bh = bbox
    x1 = max(0, int(x))
    y1 = max(0, int(y))
    x2 = min(w, int(x + bw))
    y2 = min(h, int(y + bh))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


def _extract_face_regions(
    frame_bgr: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> np.ndarray | None:
    """Extract forehead + cheek regions for rPPG analysis."""
    clipped = _clip_bbox_to_frame(bbox, frame_bgr.shape)
    if clipped is None:
        return None

    x, y, w, h = clipped
    forehead = frame_bgr[
        int(y + 0.14 * h): int(y + 0.36 * h),
        int(x + 0.20 * w): int(x + 0.80 * w),
    ]
    left_cheek = frame_bgr[
        int(y + 0.45 * h): int(y + 0.74 * h),
        int(x + 0.12 * w): int(x + 0.38 * w),
    ]
    right_cheek = frame_bgr[
        int(y + 0.45 * h): int(y + 0.74 * h),
        int(x + 0.62 * w): int(x + 0.88 * w),
    ]

    patches = [p for p in (forehead, left_cheek, right_cheek) if p.size > 0]
    if not patches:
        return None
    return np.concatenate([p.reshape(-1, 3) for p in patches], axis=0)


def check_liveness_rppg(
    frames: list[np.ndarray],
    face_bboxes: list[tuple[int, int, int, int] | None] | None,
    *,
    fps: float = 30.0,
    min_frames: int = 30,
    signal_threshold: float = 0.3,
    band_hz: tuple[float, float] = (0.7, 4.0),
) -> tuple[bool, float, float]:
    """Estimate pulse-like signal from face ROIs.

    Returns:
        (is_live, estimated_hr_bpm, signal_quality)
    """
    if len(frames) < min_frames:
        return False, 0.0, 0.0

    if not face_bboxes or len(face_bboxes) < len(frames):
        return False, 0.0, 0.0

    channel_means: list[np.ndarray] = []
    last_valid_bbox: tuple[int, int, int, int] | None = None

    for frame, bbox in zip(frames, face_bboxes):
        current_bbox = bbox if bbox is not None else last_valid_bbox
        if current_bbox is None:
            continue

        regions = _extract_face_regions(frame, current_bbox)
        if regions is None or regions.shape[0] < 32:
            continue

        last_valid_bbox = current_bbox
        channel_means.append(np.mean(regions, axis=0).astype(np.float32))

    if len(channel_means) < min_frames:
        return False, 0.0, 0.0

    means = np.stack(channel_means, axis=0)
    # POS-like combination with stronger emphasis on green channel.
    b = means[:, 0]
    g = means[:, 1]
    r = means[:, 2]
    pulse_signal = g - 0.5 * (r + b)

    pulse_signal = pulse_signal - float(np.mean(pulse_signal))
    if float(np.std(pulse_signal)) < 1e-6:
        return False, 0.0, 0.0

    n = pulse_signal.shape[0]
    windowed = pulse_signal * np.hanning(n)
    spectrum = np.abs(np.fft.rfft(windowed)) ** 2
    freqs = np.fft.rfftfreq(n, d=(1.0 / max(fps, 1e-3)))

    physi_low, physi_high = band_hz
    physi_mask = (freqs >= physi_low) & (freqs <= physi_high)
    noise_mask = ((freqs >= 0.1) & (freqs < physi_low)) | (
        (freqs > physi_high) & (freqs <= 8.0)
    )

    if not np.any(physi_mask):
        return False, 0.0, 0.0

    physi_spectrum = spectrum[physi_mask]
    physi_freqs = freqs[physi_mask]
    peak_idx = int(np.argmax(physi_spectrum))
    peak_hz = float(physi_freqs[peak_idx])
    estimated_hr = float(peak_hz * 60.0)

    physi_power = float(np.sum(physi_spectrum))
    noise_power = float(np.sum(spectrum[noise_mask])) + 1e-6
    signal_quality = float(physi_power / noise_power)

    is_live = (
        signal_quality >= signal_threshold
        and 42.0 <= estimated_hr <= 240.0
    )
    return is_live, estimated_hr, signal_quality


def check_liveness_flash(
    frame_pre_flash: np.ndarray,
    frame_post_flash: np.ndarray,
    *,
    threshold: float = 0.5,
    face_bbox: tuple[int, int, int, int] | None = None,
) -> tuple[bool, float, str]:
    """Analyze flash/no-flash pair for skin-like subsurface scattering."""
    pre = frame_pre_flash
    post = frame_post_flash
    if pre.shape[:2] != post.shape[:2]:
        post = cv2.resize(post, (pre.shape[1], pre.shape[0]))

    if face_bbox is not None:
        clipped = _clip_bbox_to_frame(face_bbox, pre.shape)
        if clipped is None:
            return False, 0.0, "invalid_face_region"
        x, y, w, h = clipped
        pre_roi = pre[y:y + h, x:x + w]
        post_roi = post[y:y + h, x:x + w]
    else:
        # Center crop fallback if no face ROI is available.
        h, w = pre.shape[:2]
        x1 = int(w * 0.25)
        x2 = int(w * 0.75)
        y1 = int(h * 0.20)
        y2 = int(h * 0.85)
        pre_roi = pre[y1:y2, x1:x2]
        post_roi = post[y1:y2, x1:x2]

    if pre_roi.size == 0 or post_roi.size == 0:
        return False, 0.0, "empty_flash_roi"

    pre_f = pre_roi.astype(np.float32)
    post_f = post_roi.astype(np.float32)
    delta = post_f - pre_f

    delta_red = np.clip(delta[..., 2], 0.0, None)
    delta_green = np.clip(delta[..., 1], 0.0, None)
    delta_luma = (
        0.114 * delta[..., 0]
        + 0.587 * delta[..., 1]
        + 0.299 * delta[..., 2]
    )

    gray_pre = cv2.cvtColor(pre_roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray_pre, threshold1=40, threshold2=120)
    edge_mask = edges > 0
    interior_mask = ~edge_mask

    edge_red = float(np.mean(delta_red[edge_mask])) if np.any(edge_mask) else 0.0
    interior_red = (
        float(np.mean(delta_red[interior_mask]))
        if np.any(interior_mask)
        else 0.0
    )
    edge_ratio = edge_red / (interior_red + 1e-6)

    chroma_ratio = float(np.mean(delta_red)) / (float(np.mean(delta_green)) + 1e-6)
    texture_response = float(np.std(delta_luma) / (np.mean(np.abs(delta_luma)) + 1e-6))

    edge_norm = float(np.clip((edge_ratio - 1.0) / 2.0, 0.0, 1.0))
    chroma_norm = float(np.clip((chroma_ratio - 1.0) / 1.5, 0.0, 1.0))
    texture_norm = float(np.clip(texture_response / 1.5, 0.0, 1.0))

    scattering_score = float(0.45 * edge_norm + 0.20 * chroma_norm + 0.35 * texture_norm)

    if scattering_score >= threshold:
        pattern = "skin_like_scattering_pattern"
        return True, scattering_score, pattern

    if edge_norm < 0.2 and texture_norm < 0.2:
        pattern = "uniform_reflective_response_possible_screen_or_print"
    elif chroma_norm < 0.2:
        pattern = "low_chromatic_response_possible_non_skin_material"
    else:
        pattern = "insufficient_subsurface_scattering_signal"
    return False, scattering_score, pattern


# ── Unified orchestrator ───────────────────────────────────────────


def check_liveness(
    frames: list[np.ndarray],
    face_crops: list[np.ndarray] | None = None,
    face_bboxes: list[tuple[int, int, int, int] | None] | None = None,
    *,
    motion_threshold: float = 2.5,
    flow_min_magnitude: float = 0.8,
    spoof_threshold: float = 0.5,
    enable_rppg: bool | None = None,
    rppg_min_frames: int | None = None,
    rppg_signal_threshold: float | None = None,
    enable_flash: bool | None = None,
    camera_supports_flash: bool = False,
    flash_scattering_threshold: float | None = None,
    flash_frame_pair: tuple[np.ndarray, np.ndarray] | None = None,
    flash_face_bbox: tuple[int, int, int, int] | None = None,
    depth_checker: Callable[[], tuple[bool, str]] | None = None,
) -> LivenessResult:
    """Run all liveness tiers in cascade — cheapest first.

    Args:
        frames: Full-frame images sampled from the 5s clip.
        face_crops: Face crops for CNN anti-spoof (Tier 3).
        face_bboxes: Per-frame face boxes for rPPG ROI extraction.
        depth_checker: Optional extension hook for depth consistency checks.
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

    # Tier 4: rPPG physiological signal
    rppg_enabled = settings.enable_rppg_liveness if enable_rppg is None else bool(enable_rppg)
    min_frames = int(settings.rppg_min_frames if rppg_min_frames is None else rppg_min_frames)
    rppg_threshold = float(
        settings.rppg_signal_threshold
        if rppg_signal_threshold is None
        else rppg_signal_threshold
    )

    if rppg_enabled:
        if len(frames) < min_frames:
            result.tier4_status = "skipped_insufficient_frames"
            logger.info(
                "liveness: tier4_rppg skipped (frames=%d, required=%d). Recommended burst_capture_count >= %d for rPPG mode.",
                len(frames),
                min_frames,
                min_frames,
            )
        else:
            t4_pass, est_hr, signal_quality = check_liveness_rppg(
                frames,
                face_bboxes,
                min_frames=min_frames,
                signal_threshold=rppg_threshold,
            )
            result.tier4_status = "evaluated"
            result.tier4_pass = t4_pass
            result.tier4_estimated_hr_bpm = est_hr
            result.tier4_rppg_signal_quality = signal_quality
            if not t4_pass:
                result.detail = "Tier 4 failed: weak or non-physiological rPPG signal"
                return result

    # Tier 5: Flash subsurface scattering
    flash_enabled = settings.enable_flash_liveness if enable_flash is None else bool(enable_flash)
    flash_threshold = float(
        settings.flash_scattering_threshold
        if flash_scattering_threshold is None
        else flash_scattering_threshold
    )
    if flash_enabled:
        if not camera_supports_flash:
            result.tier5_status = "skipped_camera_not_supported"
        elif flash_frame_pair is None:
            result.tier5_status = "skipped_missing_flash_pair"
        else:
            t5_pass, scattering_score, pattern = check_liveness_flash(
                flash_frame_pair[0],
                flash_frame_pair[1],
                threshold=flash_threshold,
                face_bbox=flash_face_bbox,
            )
            result.tier5_status = "evaluated"
            result.tier5_pass = t5_pass
            result.tier5_scattering_score = scattering_score
            result.tier5_pattern_analysis = pattern
            if not t5_pass:
                result.detail = "Tier 5 failed: flash scattering pattern indicates possible spoof"
                return result

    # Optional depth consistency extension hook.
    if depth_checker is not None:
        try:
            depth_ok, depth_reason = depth_checker()
            if not depth_ok:
                result.detail = f"Depth consistency failed: {depth_reason}"
                return result
        except Exception as exc:
            logger.debug("liveness: depth checker error: %s", exc)

    result.is_live = True
    result.detail = "all tiers passed"
    return result
