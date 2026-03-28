# Research Candidate 1 — "PostgreSQL + Redis + SQLAlchemy 2.0 Async"

> **Persona**: web-backend-engineer (Candidate A)  
> **Date**: 2026-03-28  
> **Scope**: Database, caching layer, ORM, and API framework architecture for Attendance System V2

---

## 1. Thesis

**Evolutionary upgrade** — Preserve the proven V1 PostgreSQL + SQLAlchemy foundation but aggressively modernize it by switching to fully async, adopting pgvector-native types, and introducing Redis as a first-class caching/session layer.

---

## 2. Database: PostgreSQL 16 + pgvector 0.7

### Rationale
- V1 already uses PostgreSQL. The data model is relational (users, courses, schedules, detections) — not a graph or document problem.
- pgvector 0.7 introduces HNSW indexes for sub-linear ANN search. V1 stored embeddings as `ARRAY(Float)` performing O(n) cosine scans in NumPy. pgvector eliminates this entirely.
- PostgreSQL's `JSONB` handles audit log details, device metadata, and future extensibility without schema changes.
- `pg_cron` or APScheduler can drive periodic tasks.

### Configuration
```
- Engine: PostgreSQL 16
- Extension: pgvector 0.7.4 (HNSW index, ef_construction=200, m=16)
- Connection: asyncpg (pure Python async driver)
- Pooling: SQLAlchemy AsyncSession + pool_size=20, max_overflow=10
- Index strategy:
  - B-tree on all FK columns (default)
  - HNSW on student_embeddings.embedding (VECTOR(512), cosine)
  - GIN on audit_logs.details (JSONB)
  - Composite unique on (snapshot_id, student_id, camera_id)
```

### Embedding Search Query
```sql
SELECT s.id, s.name, 1 - (se.embedding <=> $1::vector) AS similarity
FROM student_embeddings se
JOIN students s ON s.id = se.student_id
WHERE s.is_enrolled = true
ORDER BY se.embedding <=> $1::vector
LIMIT 5;
```
- HNSW index makes this O(log n) instead of O(n). For 10K embeddings: <10ms vs >200ms.

---

## 3. Caching Layer: Redis 7 (Multi-Purpose)

### Architecture
Redis serves **four distinct purposes** — all on one cluster with logical DB separation:

| DB | Purpose | TTL | Data Type |
|----|---------|-----|-----------|
| 0 | Nonce store (replay protection) | 60s | String (SET NX EX) |
| 1 | Celery broker + result backend | Task-defined | List/Hash |
| 2 | Session/refresh token cache | 7d | Hash |
| 3 | Rate limiter counters | 30s sliding | String (INCR) |

### Key Patterns
```
nonce:{device_id}:{nonce}     → "1"         (DB 0, EX 60)
session:{user_id}             → {jwt_meta}  (DB 2, EX 604800)
rate:{device_id}              → counter     (DB 3, EX 30)
attendance:live:{schedule_id} → SSE payload (DB 3, EX 300, pub/sub)
```

### Why NOT Memcached?
- Redis pub/sub enables SSE broadcasting for live attendance
- Redis Streams could replace Celery in the future
- Single operational dependency vs two (Memcached + Redis for Celery)

---

## 4. ORM: SQLAlchemy 2.0 (Fully Async)

### Why Keep SQLAlchemy?
- V1 already has proven models. Migration cost is LOW — mainly converting `Session` → `AsyncSession`.
- SQLAlchemy 2.0's `Mapped[]` type annotations are already used in V1.
- Alembic for migrations — battle-tested, auto-generates from model diffs.
- pgvector-sqlalchemy provides native `Vector` column type.

### Async Session Pattern
```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

engine = create_async_engine(
    "postgresql+asyncpg://...",
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

### Model Migration (V1 → V2)
```python
# V1: ARRAY(Float) — O(n) NumPy scan
face_embedding = mapped_column(ARRAY(Float), nullable=True)

# V2: pgvector VECTOR — O(log n) HNSW index
from pgvector.sqlalchemy import Vector
embedding = mapped_column(Vector(512), nullable=False)
```

---

## 5. API Framework: FastAPI (Async)

### Why Keep FastAPI?
- Auto-generates OpenAPI 3.1 spec from Pydantic models
- Native async/await + WebSocket support
- V1 already uses it — team knows it
- Dependency injection system for auth middleware

### Modular Router Architecture (fixing V1's monolith)
```
backend/app/api/
├── __init__.py
├── deps.py              # get_db, get_current_user, require_role()
├── v1/
│   ├── __init__.py
│   ├── auth.py          # /auth/*
│   ├── users.py         # /users/*
│   ├── students.py      # /students/*
│   ├── courses.py       # /courses/*
│   ├── schedules.py     # /schedules/*
│   ├── rooms.py         # /rooms/*
│   ├── devices.py       # /devices/*
│   ├── ingest.py        # /ingest/*
│   ├── attendance.py    # /attendance/*
│   └── system.py        # /health, /ai/status, /audit-logs
```

---

## 6. Migration Strategy

| Step | Action | Risk |
|------|--------|------|
| 1 | Create async engine + AsyncSession factory | Low — additive |
| 2 | Convert `entities.py` to use `Vector(512)` | Low — Alembic handles |
| 3 | Add `users`, `courses`, `audit_logs`, `refresh_tokens` tables | Low — new tables |
| 4 | Split `routes.py` into modular routers | Medium — large refactor |
| 5 | Add auth middleware + RBAC decorators | Medium — cross-cutting |
| 6 | Replace `psycopg2` with `asyncpg` | Low — driver swap |

---

## 7. Strengths

1. **Lowest migration risk** — evolutionary upgrade of proven V1 stack
2. **Single database** — PostgreSQL handles relational data AND vector search
3. **Mature ecosystem** — SQLAlchemy, Alembic, asyncpg all production-hardened
4. **Team familiarity** — V1 already uses this stack
5. **pgvector HNSW** — native ANN search eliminates NumPy O(n) bottleneck

## 8. Weaknesses

1. **SQLAlchemy overhead** — ORM adds ~15% latency vs raw asyncpg queries
2. **No query builder agility** — complex analytics queries are verbose in ORM
3. **pgvector maturity** — less mature than dedicated vector DBs (Pinecone, Weaviate)
4. **Alembic async** — migration runner is synchronous (requires workaround for async engine)
5. **Redis complexity** — 4 logical DBs on one instance is operationally messy at scale
