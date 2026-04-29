"""Person-level ReID embedding service for cross-camera linking."""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from backend.core.config import build_onnx_execution_providers, get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ReIDService:
    """Loads ONNX ReID model and extracts normalized person embeddings."""

    def __init__(self) -> None:
        self._session = None
        self._loaded = False
        self._input_name: str | None = None
        self._input_size: tuple[int, int] = (128, 256)

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        model_path = Path(settings.reid_model_path)
        if not model_path.exists():
            logger.warning("reid: model not found at %s", model_path)
            return

        try:
            import onnxruntime as ort

            providers = build_onnx_execution_providers(settings.insightface_provider)
            self._session = ort.InferenceSession(str(model_path), providers=providers)

            input_meta = self._session.get_inputs()[0]
            self._input_name = input_meta.name

            shape = list(getattr(input_meta, "shape", []) or [])
            if len(shape) == 4:
                h, w = shape[2], shape[3]
                if isinstance(h, int) and isinstance(w, int) and h > 0 and w > 0:
                    self._input_size = (int(w), int(h))

            logger.info(
                "reid: model loaded path=%s providers=%s input_size=%s",
                model_path,
                self._session.get_providers(),
                self._input_size,
            )
        except Exception as exc:
            logger.warning("reid: failed to initialize model: %s", exc)
            self._session = None

    @staticmethod
    def _normalize(vec: np.ndarray) -> np.ndarray | None:
        flat = np.asarray(vec, dtype=np.float32).flatten()
        if flat.size == 0 or not np.isfinite(flat).all():
            return None

        target_dim = int(max(32, settings.reid_embedding_dim))
        if flat.shape[0] > target_dim:
            flat = flat[:target_dim]
        elif flat.shape[0] < target_dim:
            padded = np.zeros((target_dim,), dtype=np.float32)
            padded[: flat.shape[0]] = flat
            flat = padded

        norm = float(np.linalg.norm(flat))
        if norm <= 1e-8:
            return None
        return flat / norm

    def extract_person_embedding(self, person_crop_bgr: np.ndarray) -> np.ndarray | None:
        """Extract one person-level embedding from full-body crop."""
        self.ensure_loaded()
        if self._session is None:
            return None

        try:
            input_w, input_h = self._input_size
            resized = cv2.resize(person_crop_bgr, (int(input_w), int(input_h)))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0

            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            normalized = (rgb - mean) / std

            chw = normalized.transpose(2, 0, 1)
            batch = np.expand_dims(chw, 0).astype(np.float32)

            input_name = self._input_name or self._session.get_inputs()[0].name
            outputs = self._session.run(None, {input_name: batch})
            if not outputs:
                return None
            return self._normalize(outputs[0])
        except Exception as exc:
            logger.debug("reid: embedding extraction failed: %s", exc)
            return None


reid_service = ReIDService()
