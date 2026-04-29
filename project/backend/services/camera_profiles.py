"""Camera profile loader for per-camera capability and threshold overrides."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CAMERA_PROFILES_PATH = Path(__file__).resolve().parents[1] / "data" / "camera_profiles.json"
_DEFAULT_PROFILE: dict[str, Any] = {
    "min_face_size_px": 48,
    "sahi_slice_size": 320,
    "quality_threshold": 0.18,
    "supports_flash_liveness": False,
    "supports_depth_liveness": False,
}

_cache_data: dict[str, Any] | None = None
_cache_mtime: float | None = None


def _load_profiles() -> dict[str, Any]:
    global _cache_data, _cache_mtime

    if not _CAMERA_PROFILES_PATH.exists():
        return {"default": dict(_DEFAULT_PROFILE)}

    mtime = None
    try:
        mtime = _CAMERA_PROFILES_PATH.stat().st_mtime
    except Exception:
        mtime = None

    if _cache_data is not None and mtime is not None and mtime == _cache_mtime:
        return _cache_data

    try:
        raw = json.loads(_CAMERA_PROFILES_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("camera_profiles.json must contain a JSON object")
    except Exception as exc:
        logger.warning("camera_profiles: failed to load %s: %s", _CAMERA_PROFILES_PATH, exc)
        raw = {}

    normalized: dict[str, Any] = {"default": dict(_DEFAULT_PROFILE)}
    default_raw = raw.get("default")
    if isinstance(default_raw, dict):
        normalized["default"].update(default_raw)

    for camera_key, profile in raw.items():
        if camera_key == "default" or not isinstance(profile, dict):
            continue
        merged = dict(normalized["default"])
        merged.update(profile)
        normalized[str(camera_key)] = merged

    _cache_data = normalized
    _cache_mtime = mtime
    return normalized


def get_camera_profile(camera_id: str | None) -> dict[str, Any]:
    """Return merged camera profile for the camera_id or default profile."""
    profiles = _load_profiles()
    if camera_id:
        key = str(camera_id)
        if key in profiles:
            return dict(profiles[key])
    return dict(profiles.get("default", _DEFAULT_PROFILE))


def camera_supports_flash_liveness(camera_id: str | None) -> bool:
    profile = get_camera_profile(camera_id)
    return bool(profile.get("supports_flash_liveness", False))


def camera_supports_depth_liveness(camera_id: str | None) -> bool:
    profile = get_camera_profile(camera_id)
    return bool(profile.get("supports_depth_liveness", False))
