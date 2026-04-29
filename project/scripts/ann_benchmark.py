"""ANN benchmark harness for NumPy, pgvector HNSW, and DiskANN backends.

This script generates synthetic normalized embeddings at configurable scales,
loads them into PostgreSQL, and reports latency/recall metrics per backend.

Example:
    python -m scripts.ann_benchmark --scales 1000 10000 100000 --queries 100
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from typing import Any

import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.config import get_settings

EMBEDDING_DIM = 512
DEFAULT_SCALES = [1000, 10000, 100000, 1000000]
DEPARTMENTS = ["CS", "EE", "ME", "CE", "BIO"]


@dataclass
class BackendResult:
    backend: str
    enabled: bool
    latency_ms: list[float]
    recall_at_5: float
    recall_at_10: float
    skipped_reason: str | None = None


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float32), q))


def _normalize_rows(rows: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(rows, axis=1, keepdims=True) + 1e-8
    return rows / norms


def _vector_to_pg_text(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in vec.astype(np.float32)) + "]"


def _build_filter_where(
    *,
    active_only: bool,
    exclude_quarantined: bool,
    enrollment_year: int | None,
    department: str | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = ["1=1"]
    params: dict[str, Any] = {}

    if active_only:
        clauses.append("template_status = 'active'")
        clauses.append("is_active = true")
    elif exclude_quarantined:
        clauses.append("template_status != 'quarantined'")

    if enrollment_year is not None:
        clauses.append("enrollment_year = :enrollment_year")
        params["enrollment_year"] = int(enrollment_year)

    if department:
        clauses.append("department = :department")
        params["department"] = department

    return " AND ".join(clauses), params


def _ensure_schema(conn) -> None:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    conn.execute(
        text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vectorscale') THEN
                    CREATE EXTENSION IF NOT EXISTS vectorscale;
                END IF;
            END
            $$;
            """
        )
    )
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS ann_benchmark_embeddings (
                id BIGINT PRIMARY KEY,
                embedding vector({EMBEDDING_DIM}) NOT NULL,
                template_status VARCHAR(20) NOT NULL,
                is_active BOOLEAN NOT NULL,
                enrollment_year INTEGER,
                department VARCHAR(64)
            )
            """
        )
    )


def _diskann_supported(conn) -> bool:
    return bool(
        conn.execute(text("SELECT EXISTS (SELECT 1 FROM pg_am WHERE amname = 'diskann')")).scalar()
    )


def _reset_dataset(engine: Engine) -> None:
    with engine.begin() as conn:
        _ensure_schema(conn)
        conn.execute(text("DROP INDEX IF EXISTS idx_ann_bench_hnsw"))
        conn.execute(text("DROP INDEX IF EXISTS idx_ann_bench_diskann"))
        conn.execute(text("TRUNCATE TABLE ann_benchmark_embeddings"))


def _bulk_insert_embeddings(
    engine: Engine,
    *,
    scale: int,
    memmap_path: Path,
    rng: np.random.Generator,
    batch_size: int,
) -> np.memmap:
    mmap = np.memmap(memmap_path, dtype=np.float32, mode="w+", shape=(scale, EMBEDDING_DIM))

    insert_sql = text(
        """
        INSERT INTO ann_benchmark_embeddings (
            id,
            embedding,
            template_status,
            is_active,
            enrollment_year,
            department
        )
        VALUES (
            :id,
            CAST(:embedding AS vector),
            :template_status,
            :is_active,
            :enrollment_year,
            :department
        )
        """
    )

    inserted = 0
    while inserted < scale:
        end = min(inserted + batch_size, scale)
        count = end - inserted

        vectors = rng.standard_normal((count, EMBEDDING_DIM), dtype=np.float32)
        vectors = _normalize_rows(vectors).astype(np.float32)
        mmap[inserted:end, :] = vectors

        payload: list[dict[str, Any]] = []
        for i in range(count):
            global_idx = inserted + i
            payload.append(
                {
                    "id": global_idx + 1,
                    "embedding": _vector_to_pg_text(vectors[i]),
                    "template_status": "active" if rng.random() > 0.08 else "quarantined",
                    "is_active": bool(rng.random() > 0.05),
                    "enrollment_year": int(rng.integers(2021, 2027)),
                    "department": str(DEPARTMENTS[int(rng.integers(0, len(DEPARTMENTS)))]),
                }
            )

        with engine.begin() as conn:
            conn.execute(insert_sql, payload)

        inserted = end

    mmap.flush()
    return mmap


def _build_indexes(engine: Engine) -> dict[str, bool]:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_ann_bench_hnsw
                ON ann_benchmark_embeddings
                USING hnsw (embedding vector_cosine_ops)
                """
            )
        )

        diskann_enabled = _diskann_supported(conn)
        if diskann_enabled:
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_ann_bench_diskann
                    ON ann_benchmark_embeddings
                    USING diskann (embedding vector_cosine_ops)
                    """
                )
            )

        conn.execute(text("ANALYZE ann_benchmark_embeddings"))

    return {"hnsw": True, "diskann": diskann_enabled}


def _numpy_topk(
    mmap: np.memmap,
    query_vec: np.ndarray,
    *,
    k: int,
    chunk_size: int,
) -> list[int]:
    best_scores = np.full((k,), -1e9, dtype=np.float32)
    best_ids = np.full((k,), -1, dtype=np.int64)

    total = int(mmap.shape[0])
    for start in range(0, total, chunk_size):
        end = min(start + chunk_size, total)
        chunk = np.asarray(mmap[start:end], dtype=np.float32)
        scores = chunk @ query_vec

        if scores.shape[0] <= k:
            local_idx = np.argsort(scores)
        else:
            local_idx = np.argpartition(scores, -k)[-k:]
            local_idx = local_idx[np.argsort(scores[local_idx])]

        cand_scores = scores[local_idx]
        cand_ids = local_idx.astype(np.int64) + start + 1

        merged_scores = np.concatenate([best_scores, cand_scores])
        merged_ids = np.concatenate([best_ids, cand_ids])

        if merged_scores.shape[0] <= k:
            merged_pick = np.argsort(merged_scores)
        else:
            merged_pick = np.argpartition(merged_scores, -k)[-k:]
            merged_pick = merged_pick[np.argsort(merged_scores[merged_pick])]

        best_scores = merged_scores[merged_pick]
        best_ids = merged_ids[merged_pick]

    # Return highest score first.
    order = np.argsort(best_scores)[::-1]
    return [int(best_ids[idx]) for idx in order if int(best_ids[idx]) > 0]


def _sql_topk(
    engine: Engine,
    *,
    query_vec: np.ndarray,
    k: int,
    backend: str,
    where_sql: str,
    where_params: dict[str, Any],
) -> list[int]:
    query_text = text(
        f"""
        SELECT id
        FROM ann_benchmark_embeddings
        WHERE {where_sql}
        ORDER BY embedding <=> CAST(:query_embedding AS vector)
        LIMIT :k
        """
    )

    params = {
        "query_embedding": _vector_to_pg_text(query_vec),
        "k": int(k),
        **where_params,
    }

    with engine.begin() as conn:
        if backend == "diskann":
            conn.execute(text("SET LOCAL enable_seqscan = off"))
        rows = conn.execute(query_text, params).fetchall()
    return [int(row[0]) for row in rows]


def _recall(pred: list[int], truth: list[int], k: int) -> float:
    truth_k = set(truth[:k])
    if not truth_k:
        return 0.0
    pred_k = set(pred[:k])
    return float(len(pred_k & truth_k) / len(truth_k))


def _benchmark_one_backend(
    *,
    backend: str,
    enabled: bool,
    engine: Engine,
    mmap: np.memmap,
    queries: list[np.ndarray],
    truth_top10: list[list[int]],
    where_sql: str,
    where_params: dict[str, Any],
    numpy_chunk_size: int,
) -> BackendResult:
    if not enabled:
        return BackendResult(
            backend=backend,
            enabled=False,
            latency_ms=[],
            recall_at_5=0.0,
            recall_at_10=0.0,
            skipped_reason="backend_not_available",
        )

    latencies: list[float] = []
    recalls_5: list[float] = []
    recalls_10: list[float] = []

    for idx, query in enumerate(queries):
        started = time.perf_counter()
        if backend == "numpy":
            pred = _numpy_topk(mmap, query, k=10, chunk_size=numpy_chunk_size)
        else:
            pred = _sql_topk(
                engine,
                query_vec=query,
                k=10,
                backend=backend,
                where_sql=where_sql,
                where_params=where_params,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        latencies.append(elapsed_ms)

        truth = truth_top10[idx]
        recalls_5.append(_recall(pred, truth, 5))
        recalls_10.append(_recall(pred, truth, 10))

    return BackendResult(
        backend=backend,
        enabled=True,
        latency_ms=latencies,
        recall_at_5=float(statistics.fmean(recalls_5)) if recalls_5 else 0.0,
        recall_at_10=float(statistics.fmean(recalls_10)) if recalls_10 else 0.0,
    )


def run_benchmark(
    *,
    engine: Engine,
    scales: list[int],
    query_count: int,
    batch_size: int,
    numpy_chunk_size: int,
    active_only: bool,
    exclude_quarantined: bool,
    enrollment_year: int | None,
    department: str | None,
    seed: int,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    where_sql, where_params = _build_filter_where(
        active_only=active_only,
        exclude_quarantined=exclude_quarantined,
        enrollment_year=enrollment_year,
        department=department,
    )

    all_scale_results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="ann-bench-") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)

        for scale in scales:
            _reset_dataset(engine)

            mmap_path = tmp_dir_path / f"embeddings_{scale}.dat"
            mmap = _bulk_insert_embeddings(
                engine,
                scale=scale,
                memmap_path=mmap_path,
                rng=rng,
                batch_size=batch_size,
            )
            backend_availability = _build_indexes(engine)

            query_indices = rng.integers(0, scale, size=query_count)
            queries: list[np.ndarray] = []
            for row_idx in query_indices:
                base = np.asarray(mmap[int(row_idx)], dtype=np.float32)
                noise = rng.normal(0.0, 0.01, EMBEDDING_DIM).astype(np.float32)
                probe = _normalize_rows((base + noise).reshape(1, -1)).flatten()
                queries.append(probe)

            # Ground truth for recall is exact NumPy top-10 from the full dataset.
            truth_top10 = [
                _numpy_topk(mmap, query, k=10, chunk_size=numpy_chunk_size)
                for query in queries
            ]

            backend_results = {
                "numpy": _benchmark_one_backend(
                    backend="numpy",
                    enabled=True,
                    engine=engine,
                    mmap=mmap,
                    queries=queries,
                    truth_top10=truth_top10,
                    where_sql=where_sql,
                    where_params=where_params,
                    numpy_chunk_size=numpy_chunk_size,
                ),
                "hnsw": _benchmark_one_backend(
                    backend="hnsw",
                    enabled=backend_availability["hnsw"],
                    engine=engine,
                    mmap=mmap,
                    queries=queries,
                    truth_top10=truth_top10,
                    where_sql=where_sql,
                    where_params=where_params,
                    numpy_chunk_size=numpy_chunk_size,
                ),
                "diskann": _benchmark_one_backend(
                    backend="diskann",
                    enabled=backend_availability["diskann"],
                    engine=engine,
                    mmap=mmap,
                    queries=queries,
                    truth_top10=truth_top10,
                    where_sql=where_sql,
                    where_params=where_params,
                    numpy_chunk_size=numpy_chunk_size,
                ),
            }

            per_backend_summary: dict[str, dict[str, Any]] = {}
            for backend_name, result in backend_results.items():
                per_backend_summary[backend_name] = {
                    "enabled": result.enabled,
                    "skipped_reason": result.skipped_reason,
                    "latency_p50_ms": _percentile(result.latency_ms, 50),
                    "latency_p95_ms": _percentile(result.latency_ms, 95),
                    "recall@5": result.recall_at_5,
                    "recall@10": result.recall_at_10,
                }

            all_scale_results.append(
                {
                    "scale": int(scale),
                    "rows_loaded": int(scale),
                    "query_count": int(query_count),
                    "filters": {
                        "active_only": bool(active_only),
                        "exclude_quarantined": bool(exclude_quarantined),
                        "enrollment_year": enrollment_year,
                        "department": department,
                    },
                    "backends": per_backend_summary,
                    "targets": {
                        "diskann_p95_lt_5ms": bool(
                            per_backend_summary["diskann"]["enabled"]
                            and per_backend_summary["diskann"]["latency_p95_ms"] < 5.0
                        )
                    },
                }
            )

            del mmap

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "embedding_dim": EMBEDDING_DIM,
        "scales": all_scale_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark NumPy vs HNSW vs DiskANN ANN retrieval")
    parser.add_argument("--scales", type=int, nargs="+", default=DEFAULT_SCALES)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--numpy-chunk-size", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20260410)
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument("--include-quarantined", action="store_true")
    parser.add_argument("--enrollment-year", type=int, default=None)
    parser.add_argument("--department", type=str, default=None)
    parser.add_argument("--database-url", type=str, default=None)
    parser.add_argument(
        "--output",
        type=str,
        default="backend/data/baseline/ann_benchmark.json",
    )
    args = parser.parse_args()

    settings = get_settings()
    database_url = args.database_url or settings.database_url
    sync_url = database_url.replace("+asyncpg", "")

    engine = create_engine(sync_url, pool_pre_ping=True)

    result = run_benchmark(
        engine=engine,
        scales=[int(v) for v in args.scales],
        query_count=int(args.queries),
        batch_size=int(args.batch_size),
        numpy_chunk_size=int(args.numpy_chunk_size),
        active_only=bool(args.active_only),
        exclude_quarantined=not bool(args.include_quarantined),
        enrollment_year=args.enrollment_year,
        department=args.department,
        seed=int(args.seed),
    )

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = (ROOT_DIR / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"Wrote ANN benchmark report to {output_path}")


if __name__ == "__main__":
    main()
