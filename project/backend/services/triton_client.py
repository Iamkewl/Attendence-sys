"""Triton gRPC client wrapper with short-window dynamic batching."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from backend.core.config import get_settings
from backend.services.inference_stats import inference_stats

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class _QueuedRequest:
    payload: np.ndarray
    enqueued_at: float
    event: threading.Event
    result: np.ndarray | None = None
    error: Exception | None = None


class _ModelBatcher:
    """Collects single-item requests and executes them as one Triton batch."""

    def __init__(
        self,
        *,
        owner: "TritonInferenceClient",
        model_name: str,
        input_name: str,
        output_name: str,
    ) -> None:
        self.owner = owner
        self.model_name = model_name
        self.input_name = input_name
        self.output_name = output_name
        self._queue: deque[_QueuedRequest] = deque()
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._closed = False
        self._thread.start()

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def submit(self, payload: np.ndarray) -> np.ndarray:
        req = _QueuedRequest(
            payload=payload,
            enqueued_at=time.perf_counter(),
            event=threading.Event(),
        )
        with self._cv:
            self._queue.append(req)
            self._cv.notify()

        timeout_s = max(float(settings.triton_request_timeout_ms), 50.0) / 1000.0
        done = req.event.wait(timeout=timeout_s)
        if not done:
            raise TimeoutError(
                f"triton batch timeout for model={self.model_name} timeout_ms={settings.triton_request_timeout_ms}"
            )
        if req.error is not None:
            raise req.error
        if req.result is None:
            raise RuntimeError(f"triton returned empty result for model={self.model_name}")
        return req.result

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._closed:
                    self._cv.wait(timeout=0.5)
                if self._closed:
                    return

                first = self._queue.popleft()
                batched = [first]
                deadline = time.perf_counter() + max(float(settings.triton_batch_window_ms), 1.0) / 1000.0
                max_batch = max(int(settings.triton_max_batch_size), 1)

                while len(batched) < max_batch:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    if not self._queue:
                        self._cv.wait(timeout=remaining)
                    if self._queue:
                        batched.append(self._queue.popleft())

            try:
                stacked = np.stack([r.payload for r in batched], axis=0)
                infer_started_at = time.perf_counter()
                output = self.owner._infer_raw(
                    model_name=self.model_name,
                    input_name=self.input_name,
                    output_name=self.output_name,
                    batch_input=stacked,
                )
                infer_finished_at = time.perf_counter()
                inference_stats.set_gpu_utilization(self.owner.sample_gpu_utilization())

                if output.shape[0] != len(batched):
                    raise RuntimeError(
                        f"unexpected batched output for model={self.model_name}: expected {len(batched)} rows got {output.shape[0]}"
                    )

                for idx, req in enumerate(batched):
                    req.result = np.asarray(output[idx])
                    queue_wait_ms = max((infer_started_at - req.enqueued_at) * 1000.0, 0.0)
                    total_latency_ms = max((infer_finished_at - req.enqueued_at) * 1000.0, 0.0)
                    inference_stats.record_batch(
                        model_name=self.model_name,
                        batch_size=len(batched),
                        queue_wait_ms=queue_wait_ms,
                        latency_ms=total_latency_ms,
                    )
                    req.event.set()

                logger.debug(
                    "triton batch",
                    extra={
                        "model_name": self.model_name,
                        "batch_size": len(batched),
                        "inference_ms": round((infer_finished_at - infer_started_at) * 1000.0, 3),
                    },
                )
            except Exception as exc:
                for req in batched:
                    req.error = exc
                    req.event.set()


class TritonInferenceClient:
    """High-level Triton wrapper used by AIPipeline when enable_triton is true."""

    _MODEL_SPECS: dict[str, tuple[str, str]] = {
        "yolov12": ("input", "output0"),
        "arcface": ("input", "output"),
        "adaface": ("input", "output"),
        "lvface": ("input", "output"),
        "antispoof": ("input", "output"),
        "realesrgan": ("input", "output"),
    }

    def __init__(self, url: str | None = None) -> None:
        self.url = (url or settings.triton_url).strip()
        self._client: Any | None = None
        self._grpcclient: Any | None = None
        self._batchers: dict[str, _ModelBatcher] = {}
        self._init_error: str | None = None
        self._init_client()

    def _init_client(self) -> None:
        try:
            import tritonclient.grpc as grpcclient
        except Exception as exc:
            self._init_error = f"tritonclient.grpc unavailable: {exc}"
            logger.warning("triton disabled: %s", self._init_error)
            return

        try:
            self._grpcclient = grpcclient
            self._client = grpcclient.InferenceServerClient(url=self.url, verbose=False)
            if not self._client.is_server_live():
                self._init_error = f"triton at {self.url} is not live"
                self._client = None
                return
            logger.info("triton client ready url=%s", self.url)
        except Exception as exc:
            self._init_error = f"triton init failed: {exc}"
            self._client = None
            logger.warning("triton disabled: %s", self._init_error)

    def is_available(self) -> bool:
        return self._client is not None

    def status(self) -> dict:
        return {
            "enabled": True,
            "available": self.is_available(),
            "url": self.url,
            "init_error": self._init_error,
        }

    @staticmethod
    def sample_gpu_utilization() -> float | None:
        initialized = False
        try:
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            initialized = True
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return float(util.gpu)
        except Exception:
            return None
        finally:
            if initialized:
                try:
                    pynvml.nvmlShutdown()  # type: ignore[name-defined]
                except Exception as exc:
                    logger.debug("triton_client: nvml shutdown failed: %s", exc)

    def close(self) -> None:
        for batcher in self._batchers.values():
            batcher.close()
        self._batchers.clear()

    def _get_batcher(self, model_name: str) -> _ModelBatcher:
        batcher = self._batchers.get(model_name)
        if batcher is not None:
            return batcher

        spec = self._MODEL_SPECS.get(model_name)
        if spec is None:
            raise KeyError(f"unsupported triton model={model_name}")
        input_name, output_name = spec
        batcher = _ModelBatcher(
            owner=self,
            model_name=model_name,
            input_name=input_name,
            output_name=output_name,
        )
        self._batchers[model_name] = batcher
        return batcher

    def _infer_raw(
        self,
        *,
        model_name: str,
        input_name: str,
        output_name: str,
        batch_input: np.ndarray,
    ) -> np.ndarray:
        if self._client is None or self._grpcclient is None:
            raise RuntimeError("triton is unavailable")

        grpcclient = self._grpcclient
        triton_input = grpcclient.InferInput(
            input_name,
            list(batch_input.shape),
            np_to_triton_dtype(batch_input.dtype),
        )
        triton_input.set_data_from_numpy(batch_input, binary_data=True)
        requested_output = grpcclient.InferRequestedOutput(output_name, binary_data=True)

        timeout_s = max(float(settings.triton_request_timeout_ms), 50.0) / 1000.0
        response = self._client.infer(
            model_name=model_name,
            inputs=[triton_input],
            outputs=[requested_output],
            client_timeout=timeout_s,
        )

        output = response.as_numpy(output_name)
        if output is None:
            response_meta = response.get_response()
            outputs = response_meta.get("outputs", []) if isinstance(response_meta, dict) else []
            if outputs:
                fallback_output_name = outputs[0].get("name")
                if fallback_output_name:
                    output = response.as_numpy(fallback_output_name)
        if output is None:
            raise RuntimeError(f"triton model={model_name} returned no output")
        return np.asarray(output)

    @staticmethod
    def _to_nchw_rgb_float(image_bgr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
        resized = cv2.resize(image_bgr, size)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        return rgb.transpose(2, 0, 1)

    def detect_faces(self, image_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        h, w = image_bgr.shape[:2]
        nchw = self._to_nchw_rgb_float(image_bgr, (640, 640)).astype(np.float32)
        output = self._get_batcher("yolov12").submit(nchw)

        detections = np.asarray(output)
        if detections.ndim == 3:
            detections = detections[0]
        if detections.ndim != 2 or detections.shape[1] < 4:
            return []

        boxes: list[tuple[int, int, int, int]] = []
        min_conf = float(settings.detector_confidence_threshold)
        for row in detections:
            conf = float(row[4]) if detections.shape[1] >= 5 else 1.0
            if conf < min_conf:
                continue
            x1, y1, x2, y2 = [float(v) for v in row[:4]]
            if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
                x1 *= float(w)
                x2 *= float(w)
                y1 *= float(h)
                y2 *= float(h)
            if x2 <= x1 or y2 <= y1:
                # fallback interpretation: cx, cy, bw, bh
                cx, cy, bw, bh = [float(v) for v in row[:4]]
                x1 = cx - (bw / 2.0)
                y1 = cy - (bh / 2.0)
                x2 = cx + (bw / 2.0)
                y2 = cy + (bh / 2.0)
            ix1, iy1 = max(int(x1), 0), max(int(y1), 0)
            ix2, iy2 = min(int(x2), w), min(int(y2), h)
            if ix2 <= ix1 or iy2 <= iy1:
                continue
            boxes.append((ix1, iy1, ix2 - ix1, iy2 - iy1))
        return boxes

    def extract_embedding(self, crop_bgr: np.ndarray, model_name: str) -> np.ndarray | None:
        selected = model_name.strip().lower()
        if selected not in {"arcface", "adaface", "lvface"}:
            raise ValueError(f"unsupported embedding model={model_name}")

        nchw = self._to_nchw_rgb_float(crop_bgr, (112, 112)).astype(np.float32)
        if selected in {"arcface", "adaface"}:
            # ArcFace/AdaFace usually expect [-1, 1] normalization.
            nchw = (nchw - 0.5) / 0.5
        else:
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)[:, None, None]
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)[:, None, None]
            nchw = (nchw - mean) / std

        output = self._get_batcher(selected).submit(nchw)
        emb = np.asarray(output, dtype=np.float32).flatten()
        if emb.size == 0:
            return None
        norm = float(np.linalg.norm(emb) + 1e-8)
        return emb / norm

    def check_antispoof(self, crop_bgr: np.ndarray) -> dict[str, float | bool]:
        nchw = self._to_nchw_rgb_float(crop_bgr, (80, 80)).astype(np.float32)
        output = self._get_batcher("antispoof").submit(nchw)
        scores = np.asarray(output, dtype=np.float32).flatten()
        if scores.size == 0:
            return {"is_live": False, "spoof_score": 1.0}

        spoof_score = float(scores[-1])
        is_live = spoof_score < float(settings.liveness_spoof_threshold)
        return {"is_live": bool(is_live), "spoof_score": spoof_score}

    def super_resolve(self, crop_bgr: np.ndarray) -> np.ndarray:
        h, w = crop_bgr.shape[:2]
        nchw = self._to_nchw_rgb_float(crop_bgr, (w, h)).astype(np.float32)
        output = self._get_batcher("realesrgan").submit(nchw)
        sr = np.asarray(output, dtype=np.float32)
        if sr.ndim == 3:
            sr = sr.transpose(1, 2, 0)
        sr = np.clip(sr, 0.0, 1.0)
        sr = (sr * 255.0).astype(np.uint8)
        return cv2.cvtColor(sr, cv2.COLOR_RGB2BGR)


def np_to_triton_dtype(np_dtype: np.dtype) -> str:
    if np_dtype == np.float32:
        return "FP32"
    if np_dtype == np.float16:
        return "FP16"
    if np_dtype == np.int64:
        return "INT64"
    if np_dtype == np.int32:
        return "INT32"
    if np_dtype == np.uint8:
        return "UINT8"
    raise TypeError(f"Unsupported numpy dtype for Triton: {np_dtype}")
