"""Raw asyncpg vector search functions.

These bypass the ORM for the face-matching hot path, eliminating
SQLAlchemy overhead on the most latency-sensitive operation.

Adopted from Research Candidate 2 — the single best architectural
idea from the Native Consensus Protocol judge evaluation.
"""

from typing import Any

import asyncpg


async def find_nearest_faces(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    k: int = 5,
    enrolled_only: bool = True,
) -> list[dict[str, Any]]:
    """Find the k nearest face embeddings using pgvector HNSW index.

    Args:
        pool: asyncpg connection pool (separate from SQLAlchemy engine).
        query_embedding: 512-d float embedding to search against.
        k: Number of nearest neighbors to return.
        enrolled_only: If True, only match against enrolled students.

    Returns:
        List of dicts with keys: student_id, name, similarity, pose_label, model_name.
    """
    enrolled_filter = "AND s.is_enrolled = true" if enrolled_only else ""

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                s.id AS student_id,
                s.name,
                1 - (se.embedding <=> $1::vector) AS similarity,
                se.pose_label,
                se.model_name
            FROM student_embeddings se
            JOIN students s ON s.id = se.student_id
            WHERE 1=1 {enrolled_filter}
            ORDER BY se.embedding <=> $1::vector
            LIMIT $2
            """,
            str(query_embedding),
            k,
        )
        return [dict(row) for row in rows]


async def find_faces_above_threshold(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    threshold: float = 0.85,
    enrolled_only: bool = True,
) -> list[dict[str, Any]]:
    """Find all face embeddings above a similarity threshold.

    Args:
        pool: asyncpg connection pool.
        query_embedding: 512-d float embedding to search against.
        threshold: Minimum cosine similarity (0.0–1.0).
        enrolled_only: If True, only match against enrolled students.

    Returns:
        List of dicts with keys: student_id, name, similarity, pose_label, model_name.
    """
    enrolled_filter = "AND s.is_enrolled = true" if enrolled_only else ""

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                s.id AS student_id,
                s.name,
                1 - (se.embedding <=> $1::vector) AS similarity,
                se.pose_label,
                se.model_name
            FROM student_embeddings se
            JOIN students s ON s.id = se.student_id
            WHERE 1 - (se.embedding <=> $1::vector) >= $2
            {enrolled_filter}
            ORDER BY se.embedding <=> $1::vector
            """,
            str(query_embedding),
            threshold,
        )
        return [dict(row) for row in rows]


async def create_vector_pool(dsn: str) -> asyncpg.Pool:
    """Create a dedicated asyncpg connection pool for vector operations.

    This pool is separate from the SQLAlchemy async engine to avoid
    ORM overhead on the hot path.

    Args:
        dsn: PostgreSQL DSN (e.g., 'postgresql://user:pass@host:5432/db').
             Note: Must use plain postgresql:// (not +asyncpg).
    """
    pool = await asyncpg.create_pool(
        dsn,
        min_size=5,
        max_size=20,
    )
    # Register pgvector type codec
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return pool
