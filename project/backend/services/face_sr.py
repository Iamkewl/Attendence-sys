"""Face super-resolution — ported from V1 (P1-2).

Uses Real-ESRGAN (ONNX) or GFPGAN when available. Falls back to
bicubic upscale if no SR model is present.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from backend.core.config import build_onnx_execution_providers, get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class FaceSuperResolver:
    """Lazy-loaded face super-resolution using ONNX Runtime.

    If the ONNX model is not found on disk the resolver silently degrades
    to a high-quality bicubic upscale.
    """

    def __init__(
        self, model_path: str = "models/realesrgan_x4.onnx", scale: int = 4
    ) -> None:
        self._model_path = Path(model_path)
        self._scale = scale
        self._session = None
        self._loaded = False
        self._gfpgan_restorer = None

    def _try_load(self) -> None:
        """Attempt to load SR model — called once lazily."""
        if self._loaded:
            return
        self._loaded = True

        # Strategy 1: Try GFPGAN (best for faces)
        try:
            from gfpgan import GFPGANer

            self._gfpgan_restorer = GFPGANer(
                model_path="GFPGANv1.4.pth",
                upscale=self._scale,
                arch="clean",
                bg_upsampler=None,
            )
            logger.info("face_sr: GFPGAN loaded successfully")
            return
        except Exception:
            logger.debug("face_sr: GFPGAN not available, trying ONNX fallback")

        # Strategy 2: Try ONNX Real-ESRGAN
        if self._model_path.exists():
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
                    "face_sr: ONNX Real-ESRGAN loaded from %s (providers=%s)",
                    self._model_path,
                    self._session.get_providers(),
                )
                return
            except Exception as exc:
                logger.warning("face_sr: failed to load ONNX model: %s", exc)

        logger.info(
            "face_sr: no SR model found — falling back to bicubic upscale. "
            "Place GFPGAN or ONNX model at %s for best accuracy.",
            self._model_path,
        )

    def upscale(self, face_crop_bgr: np.ndarray) -> np.ndarray:
        """Upscale a small face crop. Always returns a valid image."""
        self._try_load()

        if self._gfpgan_restorer is not None:
            try:
                _, _, output = self._gfpgan_restorer.enhance(
                    face_crop_bgr, paste_back=True
                )
                return output
            except Exception as exc:
                logger.warning("face_sr: GFPGAN enhance failed, trying ONNX fallback: %s", exc)

        if self._session is not None:
            try:
                return self._onnx_upscale(face_crop_bgr)
            except Exception as exc:
                logger.warning("face_sr: ONNX upscale failed, falling back to bicubic: %s", exc)

        # Fallback: high-quality bicubic
        h, w = face_crop_bgr.shape[:2]
        return cv2.resize(
            face_crop_bgr,
            (w * self._scale, h * self._scale),
            interpolation=cv2.INTER_CUBIC,
        )

    def _onnx_upscale(self, face_crop_bgr: np.ndarray) -> np.ndarray:
        """Run the ONNX Real-ESRGAN model."""
        img = face_crop_bgr.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))  # HWC -> CHW
        img = np.expand_dims(img, axis=0)

        input_name = self._session.get_inputs()[0].name
        output = self._session.run(None, {input_name: img})[0]

        output = np.squeeze(output, axis=0)
        output = np.transpose(output, (1, 2, 0))  # CHW -> HWC
        output = np.clip(output * 255.0, 0, 255).astype(np.uint8)
        return output


# Module-level singleton (lazy — no model loaded until first .upscale())
face_super_resolver = FaceSuperResolver()
