"""Runtime inference observability helpers.

Collects lightweight in-process stats used by system endpoints and structured logs.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict, deque


class InferenceStats:
    """Thread-safe rolling metrics for inference and batching behavior."""

    def __init__(self, max_samples: int = 4000) -> None:
        self._max_samples = max_samples
        self._lock = threading.Lock()
        self._latency_samples_ms: deque[float] = deque(maxlen=max_samples)
        self._queue_wait_samples_ms: deque[float] = deque(maxlen=max_samples)
        self._batch_size_distribution: defaultdict[str, Counter[int]] = defaultdict(Counter)
        self._fallback_counts: Counter[str] = Counter()
        self._last_gpu_utilization_pct: float | None = None
        self._last_update_ts: float | None = None

    @staticmethod
    def _percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        if q <= 0:
            return float(min(values))
        if q >= 100:
            return float(max(values))
        sorted_values = sorted(values)
        idx = int(round((q / 100.0) * (len(sorted_values) - 1)))
        return float(sorted_values[idx])

    def record_batch(
        self,
        *,
        model_name: str,
        batch_size: int,
        queue_wait_ms: float,
        latency_ms: float,
    ) -> None:
        now = time.time()
        with self._lock:
            self._batch_size_distribution[model_name][int(max(batch_size, 1))] += 1
            self._queue_wait_samples_ms.append(float(max(queue_wait_ms, 0.0)))
            self._latency_samples_ms.append(float(max(latency_ms, 0.0)))
            self._last_update_ts = now

    def record_fallback(self, *, model_name: str, reason: str) -> None:
        with self._lock:
            self._fallback_counts[f"{model_name}:{reason}"] += 1

    def set_gpu_utilization(self, gpu_utilization_pct: float | None) -> None:
        if gpu_utilization_pct is None:
            return
        with self._lock:
            self._last_gpu_utilization_pct = float(max(0.0, min(100.0, gpu_utilization_pct)))
            self._last_update_ts = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            latencies = list(self._latency_samples_ms)
            queue_waits = list(self._queue_wait_samples_ms)
            batch_distribution = {
                model: {str(size): int(count) for size, count in sorted(counter.items())}
                for model, counter in self._batch_size_distribution.items()
            }
            fallback_counts = {key: int(value) for key, value in self._fallback_counts.items()}
            gpu_util = self._last_gpu_utilization_pct
            updated_at = self._last_update_ts

        queue_wait_avg = float(sum(queue_waits) / len(queue_waits)) if queue_waits else 0.0
        latency_avg = float(sum(latencies) / len(latencies)) if latencies else 0.0

        return {
            "sample_count": len(latencies),
            "batch_size_distribution": batch_distribution,
            "queue_wait_time_ms": {
                "avg": queue_wait_avg,
                "p95": self._percentile(queue_waits, 95.0),
                "p99": self._percentile(queue_waits, 99.0),
                "max": float(max(queue_waits)) if queue_waits else 0.0,
            },
            "gpu_utilization_pct": gpu_util,
            "inference_latency_avg_ms": latency_avg,
            "inference_latency_p95_ms": self._percentile(latencies, 95.0),
            "inference_latency_p99_ms": self._percentile(latencies, 99.0),
            "fallback_counts": fallback_counts,
            "updated_at_epoch_s": updated_at,
        }


inference_stats = InferenceStats()
