"""Deterministic runtime smoke checks for local deployment.

Checks:
1) FastAPI startup + /health
2) Celery worker startup + task registration
3) Redis connectivity
4) PostgreSQL connectivity
5) AI pipeline model loading readiness
6) Optional FlashAttention validation
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from sqlalchemy import create_engine, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.config import get_settings
from backend.services.ai_pipeline import ai_pipeline
from backend.services.flash_attention_validator import validate_flash_attention


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    payload: dict[str, Any] | None = None


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(url: str, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                return int(response.status) == 200
        except URLError:
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    return False


def _read_stream(stream, sink: list[str]) -> None:
    for line in iter(stream.readline, ""):
        if not line:
            break
        sink.append(line.rstrip())


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def check_fastapi(timeout_seconds: int) -> tuple[CheckResult, subprocess.Popen | None, int]:
    port = _find_free_port()
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    ok = _wait_for_health(f"http://127.0.0.1:{port}/health", timeout_seconds)
    if ok:
        return (
            CheckResult(name="fastapi_health", passed=True, detail=f"/health responded on port {port}"),
            proc,
            port,
        )

    output = ""
    if proc.stdout is not None:
        output = "\n".join(proc.stdout.readlines()[-20:])
    _terminate_process(proc)
    return (
        CheckResult(
            name="fastapi_health",
            passed=False,
            detail="FastAPI did not become healthy in time",
            payload={"logs_tail": output},
        ),
        None,
        port,
    )


def check_celery(timeout_seconds: int) -> tuple[CheckResult, subprocess.Popen | None]:
    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "backend.workers.celery_app:celery_app",
        "worker",
        "--pool",
        "solo",
        "--loglevel",
        "INFO",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    lines: list[str] = []
    reader = None
    if proc.stdout is not None:
        reader = threading.Thread(target=_read_stream, args=(proc.stdout, lines), daemon=True)
        reader.start()

    ready = False
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if any("ready" in line.lower() for line in lines[-40:]):
            ready = True
            break
        time.sleep(0.5)

    if not ready:
        _terminate_process(proc)
        return (
            CheckResult(
                name="celery_worker",
                passed=False,
                detail="Celery worker did not become ready",
                payload={"logs_tail": lines[-40:]},
            ),
            None,
        )

    try:
        from backend.workers.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=3)
        registered = inspect.registered() or {}
        task_names = sorted({task for tasks in registered.values() for task in tasks})
    except Exception as exc:
        _terminate_process(proc)
        return (
            CheckResult(
                name="celery_tasks",
                passed=False,
                detail=f"Failed to inspect registered tasks: {exc}",
                payload={"logs_tail": lines[-40:]},
            ),
            None,
        )

    target_tasks = {
        "backend.workers.cv_tasks.process_snapshot",
        "backend.workers.cv_tasks.process_clip",
    }
    missing = sorted(task for task in target_tasks if task not in task_names)
    if missing:
        _terminate_process(proc)
        return (
            CheckResult(
                name="celery_tasks",
                passed=False,
                detail=f"Missing expected tasks: {', '.join(missing)}",
                payload={"registered_task_count": len(task_names), "logs_tail": lines[-40:]},
            ),
            None,
        )

    return (
        CheckResult(
            name="celery_tasks",
            passed=True,
            detail="Celery worker ready and task registration verified",
            payload={"registered_task_count": len(task_names)},
        ),
        proc,
    )


def check_redis() -> CheckResult:
    settings = get_settings()
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url)
        pong = client.ping()
        client.close()
        if not pong:
            return CheckResult(name="redis", passed=False, detail="PING returned false")
        return CheckResult(name="redis", passed=True, detail="Redis ping ok")
    except Exception as exc:
        return CheckResult(name="redis", passed=False, detail=f"Redis check failed: {exc}")


def check_postgres() -> CheckResult:
    settings = get_settings()
    sync_url = settings.database_url.replace("+asyncpg", "")
    try:
        engine = create_engine(sync_url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return CheckResult(name="postgres", passed=True, detail="PostgreSQL SELECT 1 ok")
    except Exception as exc:
        return CheckResult(name="postgres", passed=False, detail=f"PostgreSQL check failed: {exc}")


def check_pipeline_load() -> CheckResult:
    try:
        ai_pipeline.ensure_loaded()
        readiness = ai_pipeline.readiness()
        model_count = sum(
            1
            for key in (
                "recognizer_loaded",
                "detector_loaded",
                "detector_fine_loaded",
                "adaface_available",
                "sr_func_available",
            )
            if readiness.get(key)
        )
        return CheckResult(
            name="ai_pipeline",
            passed=bool(readiness.get("recognizer_loaded") and readiness.get("detector_loaded")),
            detail="AIPipeline loaded",
            payload={"model_count": model_count, "readiness": readiness},
        )
    except Exception as exc:
        return CheckResult(name="ai_pipeline", passed=False, detail=f"Pipeline load failed: {exc}")


def check_flash_attention(enabled: bool) -> CheckResult:
    if not enabled:
        return CheckResult(name="flash_attention", passed=True, detail="Skipped by flag")

    result = validate_flash_attention()
    passed = result.status in {"pass", "skip"}
    return CheckResult(
        name="flash_attention",
        passed=passed,
        detail=result.reason,
        payload=result.as_dict(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local runtime smoke checks")
    parser.add_argument("--startup-timeout", type=int, default=120)
    parser.add_argument("--check-flash-attention", action="store_true")
    parser.add_argument(
        "--require-flash-pass",
        action="store_true",
        help="Fail if flash-attention result is fail or skip",
    )
    args = parser.parse_args()

    checks: list[CheckResult] = []
    fastapi_proc = None
    celery_proc = None

    try:
        fastapi_result, fastapi_proc, _ = check_fastapi(timeout_seconds=args.startup_timeout)
        checks.append(fastapi_result)

        celery_result, celery_proc = check_celery(timeout_seconds=args.startup_timeout)
        checks.append(celery_result)

        checks.append(check_redis())
        checks.append(check_postgres())
        checks.append(check_pipeline_load())

        flash_result = check_flash_attention(enabled=args.check_flash_attention)
        checks.append(flash_result)

        all_passed = all(item.passed for item in checks)
        if args.require_flash_pass and args.check_flash_attention:
            flash_status = (flash_result.payload or {}).get("status")
            if flash_status != "pass":
                all_passed = False

        summary = {
            "timestamp": datetime_now_iso(),
            "all_passed": all_passed,
            "checks": [
                {
                    "name": item.name,
                    "passed": item.passed,
                    "detail": item.detail,
                    "payload": item.payload,
                }
                for item in checks
            ],
        }
        print(json.dumps(summary, indent=2))
        return 0 if all_passed else 1
    finally:
        if celery_proc is not None:
            _terminate_process(celery_proc)
        if fastapi_proc is not None:
            _terminate_process(fastapi_proc)


def datetime_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


if __name__ == "__main__":
    raise SystemExit(main())
