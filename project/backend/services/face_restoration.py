"""Face restoration service for policy-gated CodeFormer enhancement.

The service is lazy-loaded and safe-by-default:
- If model loading fails, callers get the original crop unchanged.
- If fidelity-weight input is unsupported by the model graph, inference still runs.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from backend.core.config import build_onnx_execution_providers, get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class CodeFormerService:
    """Lazy ONNX Runtime wrapper around a CodeFormer restoration graph."""

    def __init__(self, model_path: str = "models/codeformer_v0.1.0.onnx") -> None:
        self._model_path = Path(model_path)
        self._session = None
        self._loaded = False
        self._input_name: str | None = None
        self._fidelity_input_name: str | None = None
        self._target_size: tuple[int, int] | None = None

    def _try_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        if not self._model_path.exists():
            logger.info(
                "face_restoration: CodeFormer model not found at %s (feature remains disabled)",
                self._model_path,
            )
            return

        try:
            import onnxruntime as ort

            provider_chain = build_onnx_execution_providers(settings.insightface_provider)
            self._session = ort.InferenceSession(str(self._model_path), providers=provider_chain)

            inputs = self._session.get_inputs()
            if not inputs:
                logger.warning("face_restoration: CodeFormer model has no inputs")
                self._session = None
                return

            self._input_name = inputs[0].name
            for meta in inputs[1:]:
                candidate = meta.name.lower()
                if (
                    "fidelity" in candidate
                    or candidate in {"w", "weight"}
                    or "adain" in candidate
                ):
                    self._fidelity_input_name = meta.name
                    break

            shape = list(getattr(inputs[0], "shape", []) or [])
            if len(shape) == 4:
                h, w = shape[2], shape[3]
                if isinstance(h, int) and isinstance(w, int) and h > 0 and w > 0:
                    self._target_size = (int(w), int(h))

            logger.info(
                "face_restoration: CodeFormer ONNX loaded from %s (providers=%s, target_size=%s)",
                self._model_path,
                self._session.get_providers(),
                self._target_size,
            )
        except Exception as exc:
            self._session = None
            logger.warning("face_restoration: failed to load CodeFormer ONNX model: %s", exc)

    @staticmethod
    def _to_chw_rgb(image_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        return np.expand_dims(np.transpose(rgb, (2, 0, 1)), axis=0)

    @staticmethod
    def _from_model_output(output: np.ndarray) -> np.ndarray:
        data = np.asarray(output, dtype=np.float32)
        if data.ndim == 4:
            data = data[0]
        if data.ndim == 3 and data.shape[0] in (1, 3):
            data = np.transpose(data, (1, 2, 0))
        if data.ndim == 2:
            data = np.stack([data, data, data], axis=-1)

        min_val = float(np.min(data))
        max_val = float(np.max(data))
        if min_val < 0.0:
            # Common generator output range [-1, 1].
            data = (data + 1.0) * 0.5
        if max_val <= 1.5:
            data = data * 255.0

        data = np.clip(data, 0.0, 255.0).astype(np.uint8)
        return cv2.cvtColor(data, cv2.COLOR_RGB2BGR)

    def restore(self, crop_bgr: np.ndarray, fidelity_weight: float) -> np.ndarray:
        """Restore degraded face crop with CodeFormer.

        Args:
            crop_bgr: Input face crop in BGR format.
            fidelity_weight: Identity-preservation weight. Higher values preserve
                original identity structure more, lower values restore more aggressively.
        """
        if crop_bgr.size == 0:
            return crop_bgr

        self._try_load()
        if self._session is None or self._input_name is None:
            return crop_bgr

        try:
            original_h, original_w = crop_bgr.shape[:2]
            target = crop_bgr
            if self._target_size is not None:
                target = cv2.resize(crop_bgr, self._target_size, interpolation=cv2.INTER_CUBIC)

            inputs: dict[str, np.ndarray] = {
                self._input_name: self._to_chw_rgb(target),
            }
            if self._fidelity_input_name is not None:
                fidelity = float(max(0.0, min(1.0, fidelity_weight)))
                inputs[self._fidelity_input_name] = np.asarray([fidelity], dtype=np.float32)

            output = self._session.run(None, inputs)[0]
            restored = self._from_model_output(output)
            if restored.shape[:2] != (original_h, original_w):
                restored = cv2.resize(restored, (original_w, original_h), interpolation=cv2.INTER_CUBIC)
            return restored
        except Exception as exc:
            logger.debug("face_restoration: CodeFormer inference failed: %s", exc)
            return crop_bgr


# Module-level singleton (lazy-loaded)
codeformer_service = CodeFormerService(model_path=settings.codeformer_model_path)
