"""Face preprocessing pipeline — ported from V1 (P1-1).

Provides white-balance, adaptive gamma, LAB-CLAHE, and an orchestrator
that chains them before embedding extraction. Every stage is pure
OpenCV — no extra dependencies.
"""

from __future__ import annotations

import cv2
import numpy as np


def auto_white_balance(image_bgr: np.ndarray) -> np.ndarray:
    """Gray-world white-balance correction.

    Scales each channel so that its mean equals the overall mean brightness.
    """
    result = image_bgr.astype(np.float32)
    avg_b, avg_g, avg_r = (result[:, :, i].mean() for i in range(3))
    avg_all = (avg_b + avg_g + avg_r) / 3.0
    if avg_b > 0:
        result[:, :, 0] *= avg_all / avg_b
    if avg_g > 0:
        result[:, :, 1] *= avg_all / avg_g
    if avg_r > 0:
        result[:, :, 2] *= avg_all / avg_r
    return np.clip(result, 0, 255).astype(np.uint8)


def adaptive_gamma_correction(image_bgr: np.ndarray) -> np.ndarray:
    """Adjust gamma based on average brightness.

    Dark images get brightened (gamma < 1), overexposed images get
    darkened (gamma > 1). Normal-brightness images pass through.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    mean_brightness = float(gray.mean())

    if mean_brightness < 80:
        gamma = 0.5
    elif mean_brightness < 120:
        gamma = 0.75
    elif mean_brightness > 200:
        gamma = 1.6
    elif mean_brightness > 170:
        gamma = 1.3
    else:
        return image_bgr

    table = np.array(
        [((i / 255.0) ** gamma) * 255 for i in range(256)],
        dtype=np.uint8,
    )
    return cv2.LUT(image_bgr, table)


def clahe_lab(
    image_bgr: np.ndarray,
    clip_limit: float = 3.0,
    tile_size: int = 8,
) -> np.ndarray:
    """Apply CLAHE on the L-channel of LAB colour space.

    Superior to grayscale CLAHE — preserves colour while enhancing contrast.
    """
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=clip_limit, tileGridSize=(tile_size, tile_size)
    )
    l_channel = clahe.apply(l_channel)
    merged = cv2.merge([l_channel, a_channel, b_channel])
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def preprocess_face_crop(
    crop_bgr: np.ndarray,
    *,
    enable_sr: bool = True,
    min_upscale_px: int = 64,
    sr_func=None,
) -> np.ndarray:
    """Full preprocessing pipeline for a face crop before embedding extraction.

    Stages:
    1. White balance correction
    2. Adaptive gamma correction
    3. LAB-CLAHE contrast enhancement
    4. Super-resolution for tiny faces (optional)
    """
    crop_bgr = auto_white_balance(crop_bgr)
    crop_bgr = adaptive_gamma_correction(crop_bgr)
    crop_bgr = clahe_lab(crop_bgr)

    if enable_sr and sr_func is not None and min(crop_bgr.shape[:2]) < min_upscale_px:
        crop_bgr = sr_func(crop_bgr)

    return crop_bgr
