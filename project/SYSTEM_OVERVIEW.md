# Attendance System V2 — Distributed Facial-Recognition Attendance Platform

**Version 2.0.0** | Python 3.12+ / React 19

Attendance System V2 is an AI-powered platform that combines FastAPI, asynchronous PostgreSQL vector search, Celery-based CV workers, and a React 19 frontend to automate attendance using facial recognition. The system supports LVFace Vision Transformer recognition, temporal tracking, cross-camera re-identification, 5-tier liveness detection, and a full governance toolkit — all configurable via 70+ runtime feature flags.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API Framework | FastAPI 0.115+ (async, auto-OpenAPI) |
| ORM | SQLAlchemy 2.0 Async + raw asyncpg for vector hot path |
| Database | PostgreSQL 16 + pgvector 0.7 (HNSW + DiskANN) |
| Cache / Broker | Redis 7 Alpine |
| Task Queue | Celery with Redis broker |
| Auth | PyJWT (HS256) + Argon2id password hashing |
| Frontend | React 19 + Vite + Tailwind CSS 4 + Recharts |
| CV / ML | LVFace (ViT), Ultralytics YOLOv12, ONNX Runtime |
| GPU Inference | NVIDIA Triton Inference Server (dynamic batching, TensorRT) |
| Deployment | Docker Compose (8 services) |

---

## Architecture Diagram

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────────────────────────┐
│   Camera     │     │   React 19   │     │          PostgreSQL 16               │
│   Devices    │     │   Frontend   │◄────│  pgvector (HNSW / DiskANN)           │
└──────┬───────┘     └──────▲───────┘     └──────────────▲─────────────────────  │
       │                    │ SSE / WS                   │                       │
       │ HMAC-signed        │                            │ Async ORM             │
       │ POST               │                            │ + raw asyncpg         │
       ▼                    │                            │                       │
┌──────────────┐     ┌──────┴───────┐     ┌──────────────┴───────┐               │
│  Ingest API  │────►│  FastAPI     │────►│   Celery Workers     │               │
│  /api/v1/    │     │  App (8000)  │     │   (cv-worker x2)     │               │
│  ingest      │     │              │     │                      │               │
└──────────────┘     └──────┬───────┘     └──────────┬───────────┘               │
                            │                        │                           │
                            │                        ▼                           │
                     ┌──────┴───────┐     ┌──────────────────────┐               │
                     │    Redis 7   │◄────│   AI Pipeline        │───────────────┘
                     │  broker +    │     │   (8 stages)         │
                     │  pub/sub     │     │   LVFace ViT         │
                     └──────────────┘     │   recognition        │
                                          └──────────────────────┘
```

---

## Docker Services (8)

| Service | Image | Role | Port |
|---------|-------|------|------|
| **app** | python:3.12-slim + FastAPI | Main backend API | 8000 |
| **cv-worker** | python:3.12-slim + Celery | CV processing (x2 replicas) | — |
| **celery-beat** | python:3.12-slim | Scheduled task runner | — |
| **frontend** | node:20-alpine | Vite dev server | 5173 |
| **postgres** | pgvector/pgvector:pg16 | Vector DB + SQL | 55432 |
| **redis** | redis:7-alpine | Cache + Celery broker | 6379 |
| **triton** | nvcr.io/nvidia/tritonserver:24.03-py3 | GPU inference server | 8001-8002 |

---

## Backend Router Modules (/api/v1/)

| Module | Responsibility |
|--------|---------------|
| auth.py | JWT login / register / refresh / logout |
| students.py | Student CRUD + face enrollment (guided burst + multi-image) |
| attendance.py | Schedule-based attendance reports + CSV export |
| courses.py | Course and schedule CRUD |
| ingest.py | Device snapshot/clip ingestion (HMAC-authenticated) |
| system.py | System settings, health, AI status, governance, drift, tracks |
| sse.py | Server-Sent Events for real-time detection streaming |
| websocket.py | WebSocket for device orchestration |
| users.py | User CRUD with RBAC |

---

## AI/CV Processing Pipeline (8 Stages)

| # | Stage | Details |
|---|-------|---------|
| 1 | **Detection** | YOLOv12 SAHI dual-pass (coarse 640px + fine 320px); NMS deduplication |
| 2 | **Quality Scoring** | $0.55 \times \text{area\_score} + 0.45 \times \text{blur\_score}$ (Laplacian variance) |
| 3 | **Preprocessing** | White balance → Adaptive gamma → LAB-CLAHE → CodeFormer restoration → Real-ESRGAN SR |
| 4 | **Embedding Extraction** | LVFace (Vision Transformer, 512d) — primary recognition model via ONNX Runtime or Triton |
| 5 | **Vector Search** | DiskANN (pgvectorscale) with HNSW fallback; sub-logarithmic scaling to 100K+ vectors |
| 6 | **Score Aggregation** | Per-student best-template cosine score with retention weighting |
| 7 | **Match Decision** | Two-tier: strict threshold (0.85) OR relaxed (0.78) + margin (0.08) |
| 8 | **Output** | FaceMatch per detected student, deduplicated |

### Celery Worker Tasks

| Task | Input | Flow | Output |
|------|-------|------|--------|
| `process_snapshot` | Single frame | Detection → BoT-SORT tracking → Recognition → DB persist → Redis publish | Snapshot detections + SSE events |
| `process_clip` | Video clip | 5-tier liveness → Multi-frame voting → Recognition → DB persist | Clip-level verified detections |

---

## Tracking & Cross-Camera ReID

| Component | Details |
|-----------|---------|
| Temporal tracking | BoT-SORT per-camera tracker instances, best-frame selection |
| Track states | confirmed → unresolved → lost |
| Cross-camera ReID | OSNet person embedding + face identity fusion |
| ReID fusion weights | Face 0.15 · ReID 0.75 · Temporal 0.10 |
| Link threshold | 0.72 |
| Transition modeling | Camera transition priors loaded from JSON config |

---

## 5-Tier Liveness Detection

| Tier | Method | Model / Technique |
|------|--------|-------------------|
| 1 | Motion detection | Optical flow magnitude threshold (2.5) |
| 2 | Optical flow | Dense flow min magnitude (0.8) |
| 3 | CNN anti-spoof | MiniFASNet ONNX (80×80 input, threshold 0.5) |
| 4 | Remote PPG | Heart rate signal extraction (0.7–4.0 Hz, ≥30 frames) |
| 5 | Flash scattering | Pre/post-flash differential (chromatic + edge + texture) |

---

## Database Models

| Model | Key Fields |
|-------|-----------|
| **Student** | id, name, department, enrollment_year, is_enrolled |
| **StudentEmbedding** | student_id, embedding Vector(512), pose_label, resolution, model_name, template_status, quality metrics |
| **User** | email, password_hash (Argon2id), role (admin · instructor · student · device) |
| **RefreshToken** | token_hash (SHA256), revoked flag, expiry |
| **Course** | code, name, instructor_id, department |
| **Schedule** | course_id, room_id, start_time, end_time, days_of_week |
| **Snapshot** | schedule_id, timestamp, expected_count |
| **Detection** | snapshot_id, student_id, confidence, camera_id, track_id, cross_camera_source_track_id |
| **AuditLog** | JSONB payload with user attribution |
| **TemplateAuditLog** | Template refresh history with rollback support |
| **CameraDriftEvent** | Performance anomaly records |

### Alembic Migrations (6)

| Migration | Purpose |
|-----------|---------|
| 20260408_01 | Embedding quality columns + template lifecycle |
| 20260410_01 | Detection track_id for temporal tracking |
| 20260410_02 | Cross-camera source track ID |
| 20260410_03 | pgvectorscale DiskANN index |
| 20260410_04 | Governance tables (template audit, camera drift) |
| 20260411_01 | Detection student_id nullable (GDPR right-to-deletion) |

---

## Configuration

| Item | Value |
|------|-------|
| Implementation | 312-line Pydantic BaseSettings (`backend/core/config.py`) |
| Feature flags | 70+ toggles |
| Runtime override | `backend/data/system_settings.json` |
| Flag categories | Detection · Recognition · Tracking · Cross-Camera ReID · YOLOv12 · LVFace · Liveness (5 tiers) · CodeFormer · Super-Resolution · Triton · DiskANN · Fairness · Data Retention · Camera Drift |

---

## Frontend (React 19 + Vite)

| Page | Purpose |
|------|---------|
| Login | JWT authentication |
| Dashboard | System health + trend charts |
| Students | CRUD + face enrollment with multi-image upload |
| Attendance | Schedule reports + CSV export |
| Live Feed | Real-time SSE detection streaming |
| Testing Lab | Multi-face scene validation |
| Users | User management with RBAC |
| Settings | System config + governance panels |

- **62 API client methods** in `frontend/src/api/client.js`
- Local `useState` state management
- Auto-token refresh on 401 responses

---

## Security

| Control | Implementation |
|---------|---------------|
| Access tokens | JWT HS256, 15-minute TTL |
| Refresh tokens | 7-day TTL, rotated and revocable |
| Password hashing | Argon2id (time=3, memory=64 MB) |
| Authorization | RBAC with 4 roles enforced per endpoint |
| Device auth | HMAC SHA256 with 60-second nonce TTL |
| Rate limiting | Device ingest 30s · Login 5 attempts / 15 min |



---

## Key Numbers

| Metric | Value |
|--------|-------|
| Docker services | 8 |
| Frontend pages | 8 |
| API client methods | 62 |
| Feature flags | 70+ |
| Config file size | 312 lines |
| Recognition model | LVFace (ViT, 512d) |
| Embedding dimension | 512d |
| Liveness tiers | 5 |
| Alembic migrations | 6 |
| API router modules | 9 |
| Pipeline stages | 8 |
| Access token TTL | 15 minutes |
| Refresh token TTL | 7 days |
