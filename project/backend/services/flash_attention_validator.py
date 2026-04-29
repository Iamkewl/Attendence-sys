"""FlashAttention validation helpers for A10 deployment checks.

This module performs a lightweight synthetic attention benchmark and reports
whether enabling flash attention is stable and beneficial on the active GPU.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import importlib.util
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FlashAttentionValidationResult:
    """Structured validation result."""

    status: str  # pass | fail | skip
    reason: str
    gpu_name: str | None
    flash_attn_installed: bool
    samples: int
    latency_ms_flash_off: float | None
    latency_ms_flash_on: float | None
    latency_delta_pct: float | None
    peak_mem_mb_flash_off: float | None
    peak_mem_mb_flash_on: float | None
    peak_mem_delta_mb: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _flash_attn_installed() -> bool:
    return importlib.util.find_spec("flash_attn") is not None


def validate_flash_attention(
    *,
    samples: int = 60,
    warmup: int = 20,
    max_latency_regression_pct: float = 5.0,
) -> FlashAttentionValidationResult:
    """Validate FlashAttention behavior on CUDA A10.

    Returns:
        FlashAttentionValidationResult with status in {pass, fail, skip}.
    """

    try:
        import torch
        import torch.nn.functional as F
    except Exception as exc:
        return FlashAttentionValidationResult(
            status="skip",
            reason=f"torch unavailable: {exc}",
            gpu_name=None,
            flash_attn_installed=False,
            samples=samples,
            latency_ms_flash_off=None,
            latency_ms_flash_on=None,
            latency_delta_pct=None,
            peak_mem_mb_flash_off=None,
            peak_mem_mb_flash_on=None,
            peak_mem_delta_mb=None,
        )

    if not torch.cuda.is_available():
        return FlashAttentionValidationResult(
            status="skip",
            reason="CUDA not available",
            gpu_name=None,
            flash_attn_installed=False,
            samples=samples,
            latency_ms_flash_off=None,
            latency_ms_flash_on=None,
            latency_delta_pct=None,
            peak_mem_mb_flash_off=None,
            peak_mem_mb_flash_on=None,
            peak_mem_delta_mb=None,
        )

    gpu_name = str(torch.cuda.get_device_name(0))
    if "a10" not in gpu_name.lower():
        return FlashAttentionValidationResult(
            status="skip",
            reason=f"GPU is not A10 ({gpu_name})",
            gpu_name=gpu_name,
            flash_attn_installed=False,
            samples=samples,
            latency_ms_flash_off=None,
            latency_ms_flash_on=None,
            latency_delta_pct=None,
            peak_mem_mb_flash_off=None,
            peak_mem_mb_flash_on=None,
            peak_mem_delta_mb=None,
        )

    flash_attn_installed = _flash_attn_installed()
    if not flash_attn_installed:
        return FlashAttentionValidationResult(
            status="skip",
            reason="flash-attn package not installed",
            gpu_name=gpu_name,
            flash_attn_installed=False,
            samples=samples,
            latency_ms_flash_off=None,
            latency_ms_flash_on=None,
            latency_delta_pct=None,
            peak_mem_mb_flash_off=None,
            peak_mem_mb_flash_on=None,
            peak_mem_delta_mb=None,
        )

    if not hasattr(torch.backends, "cuda"):
        return FlashAttentionValidationResult(
            status="skip",
            reason="torch.backends.cuda unavailable",
            gpu_name=gpu_name,
            flash_attn_installed=True,
            samples=samples,
            latency_ms_flash_off=None,
            latency_ms_flash_on=None,
            latency_delta_pct=None,
            peak_mem_mb_flash_off=None,
            peak_mem_mb_flash_on=None,
            peak_mem_delta_mb=None,
        )

    if not all(
        hasattr(torch.backends.cuda, attr)
        for attr in (
            "enable_flash_sdp",
            "enable_mem_efficient_sdp",
            "enable_math_sdp",
            "flash_sdp_enabled",
            "mem_efficient_sdp_enabled",
            "math_sdp_enabled",
        )
    ):
        return FlashAttentionValidationResult(
            status="skip",
            reason="scaled_dot_product_attention backend controls unavailable",
            gpu_name=gpu_name,
            flash_attn_installed=True,
            samples=samples,
            latency_ms_flash_off=None,
            latency_ms_flash_on=None,
            latency_delta_pct=None,
            peak_mem_mb_flash_off=None,
            peak_mem_mb_flash_on=None,
            peak_mem_delta_mb=None,
        )

    device = torch.device("cuda:0")
    # Keeps benchmark small enough for smoke checks while still measuring kernels.
    q = torch.randn((8, 8, 192, 64), device=device, dtype=torch.float16)
    k = torch.randn((8, 8, 192, 64), device=device, dtype=torch.float16)
    v = torch.randn((8, 8, 192, 64), device=device, dtype=torch.float16)

    previous_flags = (
        torch.backends.cuda.flash_sdp_enabled(),
        torch.backends.cuda.mem_efficient_sdp_enabled(),
        torch.backends.cuda.math_sdp_enabled(),
    )

    def _run_once(enable_flash: bool) -> tuple[float, float]:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(device)

        torch.backends.cuda.enable_flash_sdp(enable_flash)
        torch.backends.cuda.enable_mem_efficient_sdp(not enable_flash)
        torch.backends.cuda.enable_math_sdp(True)

        for _ in range(max(warmup, 1)):
            _ = F.scaled_dot_product_attention(q, k, v, is_causal=False)
        torch.cuda.synchronize(device)

        started = time.perf_counter()
        for _ in range(max(samples, 1)):
            _ = F.scaled_dot_product_attention(q, k, v, is_causal=False)
        torch.cuda.synchronize(device)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        latency_ms = elapsed_ms / float(max(samples, 1))
        peak_mb = float(torch.cuda.max_memory_allocated(device) / (1024 * 1024))
        return latency_ms, peak_mb

    try:
        latency_off, peak_off = _run_once(enable_flash=False)
        latency_on, peak_on = _run_once(enable_flash=True)
    except Exception as exc:
        logger.warning("flash_attention_validator: benchmark failed: %s", exc)
        return FlashAttentionValidationResult(
            status="fail",
            reason=f"benchmark failed: {exc}",
            gpu_name=gpu_name,
            flash_attn_installed=True,
            samples=samples,
            latency_ms_flash_off=None,
            latency_ms_flash_on=None,
            latency_delta_pct=None,
            peak_mem_mb_flash_off=None,
            peak_mem_mb_flash_on=None,
            peak_mem_delta_mb=None,
        )
    finally:
        torch.backends.cuda.enable_flash_sdp(previous_flags[0])
        torch.backends.cuda.enable_mem_efficient_sdp(previous_flags[1])
        torch.backends.cuda.enable_math_sdp(previous_flags[2])

    latency_delta_pct = ((latency_on - latency_off) / max(latency_off, 1e-6)) * 100.0
    peak_mem_delta_mb = peak_on - peak_off

    if latency_delta_pct > max_latency_regression_pct:
        status = "fail"
        reason = (
            "FlashAttention regressed latency "
            f"({latency_delta_pct:.2f}% > {max_latency_regression_pct:.2f}%)"
        )
    else:
        status = "pass"
        reason = "FlashAttention validation passed"

    result = FlashAttentionValidationResult(
        status=status,
        reason=reason,
        gpu_name=gpu_name,
        flash_attn_installed=True,
        samples=samples,
        latency_ms_flash_off=latency_off,
        latency_ms_flash_on=latency_on,
        latency_delta_pct=latency_delta_pct,
        peak_mem_mb_flash_off=peak_off,
        peak_mem_mb_flash_on=peak_on,
        peak_mem_delta_mb=peak_mem_delta_mb,
    )

    logger.info("flash_attention_validator: %s", result.as_dict())
    return result
