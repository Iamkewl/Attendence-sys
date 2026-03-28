# Research Candidate 2 — "PostgreSQL + Dragonfly + Tortoise ORM"

> **Persona**: web-backend-engineer (Candidate B — competing strategy)  
> **Date**: 2026-03-28  
> **Scope**: Database, caching layer, ORM, and API framework architecture for Attendance System V2

---

## 1. Thesis

**Clean-slate modernization** — Replace SQLAlchemy with a lighter, natively-async ORM (Tortoise ORM) and swap Redis for Dragonfly (a drop-in Redis replacement with 25× throughput). Use the same PostgreSQL + pgvector foundation but with a drastically simplified data access layer.

---

## 2. Database: PostgreSQL 16 + pgvector 0.7 (Same)

### Rationale
PostgreSQL is the correct choice regardless of ORM — the data model is heavily relational, pgvector is the best embedded vector search for this scale.

### Key Difference: Raw SQL for Vector Queries
Instead of fighting ORM limitations for vector search, this strategy uses **raw asyncpg queries** for all embedding operations and reserves the ORM only for CRUD operations on relational entities.

```python
# Raw asyncpg for vector search (bypasses ORM entirely)
async def find_nearest(pool, embedding: list[float], k: int = 5):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.id, s.name, 1 - (se.embedding <=> $1::vector) AS similarity
            FROM student_embeddings se
            JOIN students s ON s.id = se.student_id
            WHERE s.is_enrolled = true
            ORDER BY se.embedding <=> $1::vector
            LIMIT $2
        """, str(embedding), k)
        return rows
```

**Benefit**: Zero ORM overhead on the hottest code path. No `Vector` column type mapping, no session overhead.

---

## 3. Caching Layer: Dragonfly (Redis-Compatible, Higher Throughput)

### Why Dragonfly?
- **Drop-in Redis replacement** — uses Redis wire protocol, all existing clients work unchanged
- **25× throughput** on multi-core (lock-free shared-nothing architecture)
- **Lower memory** — ~30% less memory than Redis for same dataset
- **Single binary** — no Redis Cluster complexity

### Configuration
```yaml
dragonfly:
  image: docker.dragonflydb.io/dragonflydb/dragonfly:v1.23
  ports:
    - "6379:6379"
  command: >
    --maxmemory 512mb
    --proactor_threads 4
    --default_lua_flags allow-undeclared-keys
```

### Same Key Schema as Redis
Uses identical key patterns — `nonce:{device_id}:{nonce}`, `rate:{device_id}`, Celery broker, pub/sub for SSE. **Zero code changes** to switch from Redis.

### Risk Assessment
- Dragonfly is newer (v1.0 in 2023) — less battle-tested than Redis
- Some edge-case Redis commands may behave differently
- Smaller community
- **Mitigation**: Dragonfly is a drop-in — fallback to Redis requires only changing the Docker image

---

## 4. ORM: Tortoise ORM (Natively Async)

### Why Replace SQLAlchemy?

| Factor | SQLAlchemy 2.0 Async | Tortoise ORM |
|--------|---------------------|--------------|
| Async Support | Bolted on (greenlet shim) | Native from day 1 |
| Boilerplate | High (engine, sessionmaker, Base, context mgr) | Low (single `Tortoise.init()`) |
| Model Style | Mapped[] annotations (verbose) | Django-like fields (concise) |
| Migrations | Alembic (separate tool, sync runner) | Aerich (built-in, async-native) |
| Learning Curve | Steep (Unit of Work pattern) | Gentle (Active Record) |
| pgvector | Community adapter (pgvector-sqlalchemy) | Raw SQL (already planned) |

### Model Comparison
```python
# SQLAlchemy 2.0 (V1 style — verbose)
class Students(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    detections: Mapped[list["Detections"]] = relationship(back_populates="student")

# Tortoise ORM (V2 — concise)
class Student(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=255)
    is_enrolled = fields.BooleanField(default=False)
    detections: fields.ReverseRelation["Detection"]

    class Meta:
        table = "students"
```

### Async Session — No Session at All
Tortoise ORM uses Active Record pattern — no session management, no `async with` ceremony:
```python
# Direct queries
student = await Student.get(id=1)
students = await Student.filter(is_enrolled=True).all()
await Student.create(name="Alice", is_enrolled=True)
```

---

## 5. API Framework: FastAPI (Same)

FastAPI is the correct choice regardless of ORM. Tortoise ORM has official FastAPI integration (`tortoise-orm[fastapi]`).

### Same Modular Router Architecture
```
backend/app/api/v1/
├── auth.py
├── users.py
├── students.py
├── courses.py
├── schedules.py
├── rooms.py
├── devices.py
├── ingest.py
├── attendance.py
└── system.py
```

---

## 6. Migration Strategy

| Step | Action | Risk |
|------|--------|------|
| 1 | Install Tortoise ORM + Aerich | Low |
| 2 | Rewrite all models from SQLAlchemy → Tortoise | **HIGH — full rewrite** |
| 3 | Use Aerich `init-db` to generate initial migration | Medium — new tool |
| 4 | Replace all `session.execute()` calls with Active Record queries | **HIGH — full rewrite** |
| 5 | Write raw asyncpg queries for vector search | Low |
| 6 | Replace Redis image with Dragonfly in docker-compose | Low |

---

## 7. Strengths

1. **True async-native** — no greenlet shim, no "bolt-on async" complexity
2. **Less boilerplate** — 40% fewer lines for model definitions
3. **Simpler mental model** — Active Record vs Unit of Work
4. **Dragonfly performance** — 25× throughput for nonce/rate-limiting at scale
5. **Raw SQL for hot path** — zero ORM overhead on vector search
6. **Aerich migrations** — async-native, built into ORM

## 8. Weaknesses

1. **Tortoise ORM maturity** — smaller ecosystem than SQLAlchemy (10× fewer GitHub stars)
2. **Full V1 rewrite** — every model and query must be rewritten (HIGH migration risk)
3. **Tortoise ORM gotchas** — limited support for complex JOINs, CTEs, window functions
4. **Dragonfly risk** — newer project, less production battle-testing
5. **pgvector integration** — no native Tortoise adapter, must use raw SQL (bypasses ORM)
6. **Community support** — fewer Stack Overflow answers, tutorials, and plugins
7. **Aerich maturity** — less feature-rich than Alembic (no data migrations, fewer hooks)
8. **No V1 code reuse** — zero models, zero queries carry over from V1
