# Attendance System V2

> AI-Powered Facial Recognition Attendance Platform (v2.0.0)

Attendance System V2 automates classroom attendance using LVFace Vision Transformer facial recognition, real-time event streaming, and a modern React dashboard. Built with FastAPI, PostgreSQL + pgvector, Celery workers, and Docker Compose for production deployment.

For complete technical internals, see [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md).

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Quick Start (Docker)](#quick-start-docker)
- [Quick Start (Without Docker)](#quick-start-without-docker)
- [Features](#features)
- [Project Structure](#project-structure)
- [API Documentation](#api-documentation)
- [Environment Variables](#environment-variables)
- [Development](#development)
- [Documentation](#documentation)
- [Contributing](#contributing)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+, FastAPI 0.115+, SQLAlchemy 2.0 Async, Celery |
| Database | PostgreSQL 16 + pgvector (vector embeddings) |
| Cache | Redis 7 |
| Frontend | React 19, Vite, Tailwind CSS 4 |
| AI/ML | LVFace (ViT), Ultralytics YOLOv12, ONNX Runtime |
| GPU Inference | NVIDIA Triton Inference Server |
| Deployment | Docker Compose (8 services) |

## Prerequisites

- Python 3.12+
- Node.js 20+
- PostgreSQL 16 with pgvector extension
- Redis 7
- Docker & Docker Compose (recommended)
- NVIDIA GPU (optional — enables Triton dynamic batching and TensorRT acceleration)

## Quick Start (Docker)

```bash
git clone <repo-url>
cd Attendence-sys
cp .env.example .env    # Edit with your settings
docker compose up -d
```

| Service | URL |
|---------|-----|
| API | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| API Docs (ReDoc) | http://localhost:8000/redoc |
| Frontend | http://localhost:5173 |

## Quick Start (Without Docker)

```bash
# Backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"
alembic upgrade head
uvicorn backend.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev

# Workers (separate terminal)
celery -A backend.workers.cv_tasks worker --loglevel=info
celery -A backend.workers.cv_tasks beat --loglevel=info
```

## Features

- **LVFace recognition** — Vision Transformer 512d face embeddings via ONNX Runtime / Triton
- **YOLOv12 SAHI detection** — dual-pass small and distant face support
- **BoT-SORT temporal tracking** — best-frame selection per track
- **Cross-camera re-identification** — OSNet-based person linking
- **5-tier liveness detection** — motion → optical flow → CNN → rPPG → flash
- **CodeFormer face restoration** — low-resolution face recovery
- **Real-time streaming** — SSE event streaming + WebSocket device orchestration
- **JWT authentication** — Argon2id hashing + refresh token rotation
- **Role-based access** — admin, instructor, student, device
- **Vector search** — DiskANN (pgvectorscale) nearest-neighbor with HNSW fallback
- **70+ feature flags** — runtime-tunable configuration
- **Template lifecycle** — active / backup / quarantined management
- **Governance tools** — fairness auditing, data retention, camera drift detection

## Project Structure

```
backend/
  main.py              # FastAPI app factory
  core/config.py       # Pydantic settings (70+ flags)
  api/v1/              # 9 router modules
  models/              # SQLAlchemy models
  schemas/             # Pydantic request/response schemas
  services/            # AI pipeline, tracking, ReID, restoration, governance
  workers/             # Celery tasks (snapshot + clip processing)
  db/                  # Session factory, vector search queries
frontend/
  src/pages/           # 8 pages (Dashboard, Students, Attendance, Live, etc.)
  src/api/client.js    # 62-method API client
  src/hooks/           # SSE streaming hook
alembic/versions/      # 6 schema migrations
triton_models/         # Triton Inference Server model repository
scripts/               # Benchmarking, enrollment, fairness audit tools
docs/runbooks/         # Operational runbooks
```

## API Documentation

Interactive documentation is auto-generated from FastAPI:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

Key endpoint groups:

| Prefix | Purpose |
|--------|---------|
| `/api/v1/auth` | Authentication (login, register, refresh, logout) |
| `/api/v1/students` | Student CRUD + face enrollment |
| `/api/v1/attendance` | Attendance reports + CSV export |
| `/api/v1/courses` | Course and schedule management |
| `/api/v1/ingest` | Device snapshot/clip ingestion |
| `/api/v1/system` | System settings, health, AI status, governance |
| `/api/v1/users` | User management with RBAC |

## Environment Variables

Key variables (see `.env.example` for the full list):

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `JWT_SECRET_KEY` | 64-char hex string for token signing |
| `CELERY_BROKER_URL` | Redis broker for task queue |
| `ENABLE_TRITON` | Toggle Triton inference server |
| `ENABLE_TRACKING` | Toggle BoT-SORT temporal tracking |
| `ENABLE_CROSS_CAMERA_REID` | Toggle cross-camera re-identification |
| `ENABLE_LIVENESS_CHECK` | Toggle anti-spoof detection |
| `ENABLE_LVFACE` | Toggle LVFace recognition model |
| `RECOGNITION_FUSION_MODE` | weighted_average · max_confidence · *_only |

## Development

```bash
# Run tests
pytest

# Lint
ruff check .

# Type check
mypy backend/
```

## Documentation

| Document | Description |
|----------|-------------|
| [ROADMAP.md](ROADMAP.md) | Current execution plan and acceptance gates |
| [SECURITY_REVIEW_2026-04-08.md](SECURITY_REVIEW_2026-04-08.md) | Security review and hardening notes |
| [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md) | Complete technical system overview |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architecture decisions and design |
| [REQUIREMENTS.md](REQUIREMENTS.md) | Project requirements specification |
| [docs/PROJECT_REFERENCE.md](docs/PROJECT_REFERENCE.md) | Consolidated onboarding, operations, and CV design reference |
| [docs/PHASE_STATUS.md](docs/PHASE_STATUS.md) | Consolidated implementation and validation status for phases 0-8 |
| [docs/runbooks/workspace_sync.md](docs/runbooks/workspace_sync.md) | Local and remote workspace sync operations |
| [docs/runbooks/daily_github_push.md](docs/runbooks/daily_github_push.md) | End-of-day commit and push workflow |
| [docs/runbooks/remote_server_handoff.md](docs/runbooks/remote_server_handoff.md) | Remote server setup and handoff procedure |
| [docs/runbooks/](docs/runbooks/) | Operational procedures |

## Contributing

Contributing guidelines are being prepared. Please coordinate with project maintainers before submitting major changes.

---

*Private / Proprietary*
