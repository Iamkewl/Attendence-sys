"""Raw vector search functions for asyncpg and sync SQLAlchemy sessions.

This module provides:
- HNSW-backed vector queries (pgvector)
- DiskANN-backed vector queries (pgvectorscale)
- Optional cohort and template lifecycle filters
- Sync wrappers for Celery worker hot paths
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(slots=True)
class VectorSearchFilters:
    """Optional filters for ANN retrieval."""

    active_only: bool = False
    exclude_quarantined: bool = True
    enrollment_year: int | None = None
    department: str | None = None
    model_name: str | None = None


def _normalize_filters(filters: VectorSearchFilters | None) -> VectorSearchFilters:
    return filters if filters is not None else VectorSearchFilters()


def _build_filter_sql(
    *,
    enrolled_only: bool,
    filters: VectorSearchFilters,
    async_mode: bool,
    query_param_idx: int,
) -> tuple[str, list[Any], dict[str, Any], int]:
    """Build filter WHERE clauses for asyncpg and SQLAlchemy text queries."""
    clauses: list[str] = []
    async_params: list[Any] = []
    sync_params: dict[str, Any] = {}
    idx = query_param_idx

    if enrolled_only:
        clauses.append("s.is_enrolled = true")

    if filters.model_name:
        if async_mode:
            idx += 1
            clauses.append(f"se.model_name = ${idx}")
            async_params.append(filters.model_name)
        else:
            clauses.append("se.model_name = :model_name")
            sync_params["model_name"] = filters.model_name

    if filters.active_only:
        clauses.append("se.template_status = 'active'")
        clauses.append("se.is_active = true")
    elif filters.exclude_quarantined:
        clauses.append("se.template_status != 'quarantined'")

    if filters.enrollment_year is not None:
        if async_mode:
            idx += 1
            clauses.append(f"s.enrollment_year = ${idx}")
            async_params.append(int(filters.enrollment_year))
        else:
            clauses.append("s.enrollment_year = :enrollment_year")
            sync_params["enrollment_year"] = int(filters.enrollment_year)

    if filters.department:
        if async_mode:
            idx += 1
            clauses.append(f"s.department = ${idx}")
            async_params.append(str(filters.department))
        else:
            clauses.append("s.department = :department")
            sync_params["department"] = str(filters.department)

    where_sql = " AND ".join(clauses) if clauses else "1=1"
    return where_sql, async_params, sync_params, idx


async def _find_nearest_async(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    k: int,
    enrolled_only: bool,
    filters: VectorSearchFilters | None,
    prefer_diskann: bool,
) -> list[dict[str, Any]]:
    filters = _normalize_filters(filters)
    where_sql, filter_params, _, idx = _build_filter_sql(
        enrolled_only=enrolled_only,
        filters=filters,
        async_mode=True,
        query_param_idx=1,
    )
    query_param = str(query_embedding)
    limit_idx = idx + 1

    sql = f"""
        SELECT
            s.id AS student_id,
            s.name,
            s.department,
            s.enrollment_year,
            1 - (se.embedding <=> $1::vector) AS similarity,
            se.pose_label,
            se.model_name,
            COALESCE(se.retention_score, 0.0) AS retention_score,
            se.template_status,
            se.is_active
        FROM student_embeddings se
        JOIN students s ON s.id = se.student_id
        WHERE {where_sql}
        ORDER BY se.embedding <=> $1::vector
        LIMIT ${limit_idx}
    """
    params = [query_param, *filter_params, int(k)]

    async with pool.acquire() as conn:
        async with conn.transaction():
            if prefer_diskann:
                await conn.execute("SET LOCAL enable_seqscan = off")
            rows = await conn.fetch(sql, *params)
    return [dict(row) for row in rows]


async def _find_above_threshold_async(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    threshold: float,
    enrolled_only: bool,
    filters: VectorSearchFilters | None,
    prefer_diskann: bool,
) -> list[dict[str, Any]]:
    filters = _normalize_filters(filters)
    where_sql, filter_params, _, idx = _build_filter_sql(
        enrolled_only=enrolled_only,
        filters=filters,
        async_mode=True,
        query_param_idx=2,
    )

    sql = f"""
        SELECT
            s.id AS student_id,
            s.name,
            s.department,
            s.enrollment_year,
            1 - (se.embedding <=> $1::vector) AS similarity,
            se.pose_label,
            se.model_name,
            COALESCE(se.retention_score, 0.0) AS retention_score,
            se.template_status,
            se.is_active
        FROM student_embeddings se
        JOIN students s ON s.id = se.student_id
        WHERE 1 - (se.embedding <=> $1::vector) >= $2
          AND {where_sql}
        ORDER BY se.embedding <=> $1::vector
    """
    params = [str(query_embedding), float(threshold), *filter_params]

    async with pool.acquire() as conn:
        async with conn.transaction():
            if prefer_diskann:
                await conn.execute("SET LOCAL enable_seqscan = off")
            rows = await conn.fetch(sql, *params)
    return [dict(row) for row in rows]


def _find_nearest_sync(
    session: Session,
    query_embedding: list[float],
    *,
    k: int,
    enrolled_only: bool,
    filters: VectorSearchFilters | None,
    prefer_diskann: bool,
) -> list[dict[str, Any]]:
    filters = _normalize_filters(filters)
    where_sql, _, sync_params, _ = _build_filter_sql(
        enrolled_only=enrolled_only,
        filters=filters,
        async_mode=False,
        query_param_idx=0,
    )

    sql = text(
        f"""
        SELECT
            s.id AS student_id,
            s.name,
            s.department,
            s.enrollment_year,
            1 - (se.embedding <=> CAST(:query_embedding AS vector)) AS similarity,
            se.pose_label,
            se.model_name,
            COALESCE(se.retention_score, 0.0) AS retention_score,
            se.template_status,
            se.is_active
        FROM student_embeddings se
        JOIN students s ON s.id = se.student_id
        WHERE {where_sql}
        ORDER BY se.embedding <=> CAST(:query_embedding AS vector)
        LIMIT :k
        """
    )
    params: dict[str, Any] = {
        "query_embedding": str(query_embedding),
        "k": int(k),
        **sync_params,
    }

    if prefer_diskann:
        session.execute(text("SET LOCAL enable_seqscan = off"))

    rows = session.execute(sql, params)
    return [dict(row._mapping) for row in rows]


def _find_above_threshold_sync(
    session: Session,
    query_embedding: list[float],
    *,
    threshold: float,
    enrolled_only: bool,
    filters: VectorSearchFilters | None,
    prefer_diskann: bool,
) -> list[dict[str, Any]]:
    filters = _normalize_filters(filters)
    where_sql, _, sync_params, _ = _build_filter_sql(
        enrolled_only=enrolled_only,
        filters=filters,
        async_mode=False,
        query_param_idx=0,
    )

    sql = text(
        f"""
        SELECT
            s.id AS student_id,
            s.name,
            s.department,
            s.enrollment_year,
            1 - (se.embedding <=> CAST(:query_embedding AS vector)) AS similarity,
            se.pose_label,
            se.model_name,
            COALESCE(se.retention_score, 0.0) AS retention_score,
            se.template_status,
            se.is_active
        FROM student_embeddings se
        JOIN students s ON s.id = se.student_id
        WHERE 1 - (se.embedding <=> CAST(:query_embedding AS vector)) >= :threshold
          AND {where_sql}
        ORDER BY se.embedding <=> CAST(:query_embedding AS vector)
        """
    )
    params: dict[str, Any] = {
        "query_embedding": str(query_embedding),
        "threshold": float(threshold),
        **sync_params,
    }

    if prefer_diskann:
        session.execute(text("SET LOCAL enable_seqscan = off"))

    rows = session.execute(sql, params)
    return [dict(row._mapping) for row in rows]


async def find_nearest_faces(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    k: int = 5,
    enrolled_only: bool = True,
    filters: VectorSearchFilters | None = None,
) -> list[dict[str, Any]]:
    """Find nearest faces using pgvector path (typically HNSW index)."""
    return await _find_nearest_async(
        pool,
        query_embedding,
        k=k,
        enrolled_only=enrolled_only,
        filters=filters,
        prefer_diskann=False,
    )


async def find_faces_above_threshold(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    threshold: float = 0.85,
    enrolled_only: bool = True,
    filters: VectorSearchFilters | None = None,
) -> list[dict[str, Any]]:
    """Find all faces above a similarity threshold (pgvector path)."""
    return await _find_above_threshold_async(
        pool,
        query_embedding,
        threshold=threshold,
        enrolled_only=enrolled_only,
        filters=filters,
        prefer_diskann=False,
    )


async def find_nearest_faces_diskann(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    k: int = 5,
    enrolled_only: bool = True,
    filters: VectorSearchFilters | None = None,
) -> list[dict[str, Any]]:
    """Find nearest faces while preferring DiskANN execution path."""
    return await _find_nearest_async(
        pool,
        query_embedding,
        k=k,
        enrolled_only=enrolled_only,
        filters=filters,
        prefer_diskann=True,
    )


async def find_faces_above_threshold_diskann(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    threshold: float = 0.85,
    enrolled_only: bool = True,
    filters: VectorSearchFilters | None = None,
) -> list[dict[str, Any]]:
    """Find all faces above threshold while preferring DiskANN path."""
    return await _find_above_threshold_async(
        pool,
        query_embedding,
        threshold=threshold,
        enrolled_only=enrolled_only,
        filters=filters,
        prefer_diskann=True,
    )


def find_nearest_faces_sync(
    session: Session,
    query_embedding: list[float],
    *,
    k: int = 5,
    enrolled_only: bool = True,
    filters: VectorSearchFilters | None = None,
) -> list[dict[str, Any]]:
    """Sync nearest-neighbor query path for Celery workers (HNSW/pgvector)."""
    return _find_nearest_sync(
        session,
        query_embedding,
        k=k,
        enrolled_only=enrolled_only,
        filters=filters,
        prefer_diskann=False,
    )


def find_faces_above_threshold_sync(
    session: Session,
    query_embedding: list[float],
    *,
    threshold: float = 0.85,
    enrolled_only: bool = True,
    filters: VectorSearchFilters | None = None,
) -> list[dict[str, Any]]:
    """Sync threshold query path for Celery workers (HNSW/pgvector)."""
    return _find_above_threshold_sync(
        session,
        query_embedding,
        threshold=threshold,
        enrolled_only=enrolled_only,
        filters=filters,
        prefer_diskann=False,
    )


def find_nearest_faces_diskann_sync(
    session: Session,
    query_embedding: list[float],
    *,
    k: int = 5,
    enrolled_only: bool = True,
    filters: VectorSearchFilters | None = None,
) -> list[dict[str, Any]]:
    """Sync nearest-neighbor query path that prefers DiskANN index usage."""
    return _find_nearest_sync(
        session,
        query_embedding,
        k=k,
        enrolled_only=enrolled_only,
        filters=filters,
        prefer_diskann=True,
    )


def find_faces_above_threshold_diskann_sync(
    session: Session,
    query_embedding: list[float],
    *,
    threshold: float = 0.85,
    enrolled_only: bool = True,
    filters: VectorSearchFilters | None = None,
) -> list[dict[str, Any]]:
    """Sync threshold query path that prefers DiskANN index usage."""
    return _find_above_threshold_sync(
        session,
        query_embedding,
        threshold=threshold,
        enrolled_only=enrolled_only,
        filters=filters,
        prefer_diskann=True,
    )


def is_diskann_ready_sync(session: Session) -> bool:
    """Check whether vectorscale extension and DiskANN index are present."""
    extension_sql = text(
        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vectorscale')"
    )
    index_sql = text(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = ANY (current_schemas(false))
              AND tablename = 'student_embeddings'
              AND indexname = 'idx_embeddings_diskann'
        )
        """
    )
    extension_exists = bool(session.execute(extension_sql).scalar())
    index_exists = bool(session.execute(index_sql).scalar())
    return extension_exists and index_exists


def explain_nearest_faces_sync(
    session: Session,
    query_embedding: list[float],
    *,
    k: int = 5,
    enrolled_only: bool = True,
    filters: VectorSearchFilters | None = None,
    prefer_diskann: bool = False,
) -> list[str]:
    """Return EXPLAIN ANALYZE text for nearest-neighbor query plan verification."""
    filters = _normalize_filters(filters)
    where_sql, _, sync_params, _ = _build_filter_sql(
        enrolled_only=enrolled_only,
        filters=filters,
        async_mode=False,
        query_param_idx=0,
    )
    sql = text(
        f"""
        EXPLAIN (ANALYZE, BUFFERS)
        SELECT s.id
        FROM student_embeddings se
        JOIN students s ON s.id = se.student_id
        WHERE {where_sql}
        ORDER BY se.embedding <=> CAST(:query_embedding AS vector)
        LIMIT :k
        """
    )
    params = {
        "query_embedding": str(query_embedding),
        "k": int(k),
        **sync_params,
    }
    if prefer_diskann:
        session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = session.execute(sql, params)
    return [str(row[0]) for row in rows]


async def create_vector_pool(dsn: str) -> asyncpg.Pool:
    """Create a dedicated asyncpg connection pool for vector operations."""
    pool = await asyncpg.create_pool(
        dsn,
        min_size=5,
        max_size=20,
    )
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return pool
