# REQUIREMENTS.md — Attendance System V2

> **Owner**: product-manager  
> **Date**: 2026-03-28  
> **Status**: Draft — Pending architect-coordinator approval  
> **Supersedes**: V1 `REQUIREMENTS.md` from `Attendence-system/`

---

## 1. Executive Summary

Attendance System V2 is a ground-up rebuild of the distributed facial-recognition attendance platform. V1 proved the core CV pipeline works. V2 modernizes the entire software stack while preserving all V1 AI capabilities:

| V1 Gap | V2 Solution |
|--------|-------------|
| No user authentication | JWT auth + refresh token rotation |
| No role-based access | RBAC (admin, instructor, student, device) |
| 42KB monolithic `routes.py` | Modular router architecture (per-domain) |
| Streamlit-only UI | React 19 + Vite dashboard (Streamlit kept for debug) |
| No real-time attendance feed | SSE/WebSocket live attendance stream |
| Manual-only reporting | Automated PDF/CSV reports + analytics |
| SQLAlchemy `ARRAY(Float)` embeddings | pgvector native `VECTOR(512)` with HNSW index |
| No audit trail | Full audit logging with user attribution |

---

## 2. Functional Requirements

### FR-1: User Authentication & Session Management

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1.1 | Email + password registration with email verification | P0 |
| FR-1.2 | JWT access token (15m TTL) + refresh token (7d TTL, rotated) | P0 |
| FR-1.3 | Secure password storage with Argon2id hashing | P0 |
| FR-1.4 | Login rate limiting (5 attempts / 15 min, lockout on 10 failures) | P0 |
| FR-1.5 | Logout with refresh token revocation (Redis blocklist) | P0 |
| FR-1.6 | Password reset flow via email token (1h TTL) | P1 |
| FR-1.7 | OAuth2 social login (Google) — future extensibility | P2 |

### FR-2: Role-Based Access Control (RBAC)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-2.1 | Four roles: `admin`, `instructor`, `student`, `device` | P0 |
| FR-2.2 | Admin: full CRUD on all entities, user management, system config | P0 |
| FR-2.3 | Instructor: manage own courses/schedules, view attendance for own classes, enroll students | P0 |
| FR-2.4 | Student: view own attendance records and profile | P0 |
| FR-2.5 | Device: authenticate via API key + HMAC, upload snapshots/clips only | P0 |
| FR-2.6 | Permission middleware — decorators check `role` claim from JWT | P0 |
| FR-2.7 | Admin can assign/revoke instructor <> course bindings | P1 |
| FR-2.8 | Audit log — every state-changing API call logged with `(user_id, action, resource, timestamp)` | P0 |

### FR-3: Student Enrollment (carried from V1)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-3.1 | Enroll via 5+ photos → SAHI detection → embedding extraction → robust template | P0 |
| FR-3.2 | Enroll via pre-cropped face images | P0 |
| FR-3.3 | Enroll via raw embedding vector (API) | P0 |
| FR-3.4 | Multi-pose enrollment (`frontal`, `left_34`, `right_34`) × resolution × model | P0 |
| FR-3.5 | Minimum 5 successful embeddings required | P0 |
| FR-3.6 | Student profile with photo, department, enrollment year metadata | P1 |
| FR-3.7 | Bulk enrollment via CSV + image ZIP upload | P1 |

### FR-4: Heartbeat Capture & Orchestration (carried from V1)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-4.1 | APScheduler triggers capture every N-minute boundary during active schedules | P0 |
| FR-4.2 | Orchestrator sends nonce + burst hints via WebSocket to room devices | P0 |
| FR-4.3 | Device captures burst, selects best by `0.7×face_area + 0.3×sharpness` | P0 |
| FR-4.4 | HMAC-SHA256 signed payload upload to `/api/v1/ingest` | P0 |
| FR-4.5 | Clip mode — 5s video for liveness + multi-frame voting | P0 |
| FR-4.6 | RTSP stream ingestion from IP cameras (server-side pull) | P1 |

### FR-5: Face Detection & Recognition (carried from V1)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-5.1 | Dual-pass SAHI (640+320) with NMS merge | P0 |
| FR-5.2 | Quality gate (min size, blur, quality score) | P0 |
| FR-5.3 | Preprocessing pipeline (white-balance → gamma → CLAHE → super-res) | P0 |
| FR-5.4 | ArcFace 512-d embedding extraction | P0 |
| FR-5.5 | pgvector HNSW nearest-neighbor matching | P0 |
| FR-5.6 | Two-tier threshold: strict OR relaxed + margin | P0 |
| FR-5.7 | Dedup — one sighting per student per snapshot window (±5 min) | P0 |

### FR-6: Liveness Verification (carried from V1)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-6.1 | Tier 1: frame-difference motion detection (<5ms) | P0 |
| FR-6.2 | Tier 2: Farneback optical flow (~20–50ms) | P0 |
| FR-6.3 | Tier 3: MiniFASNet CNN passive anti-spoof via ONNX | P0 |
| FR-6.4 | Cascading — cheapest-first, 90%+ terminate at Tier 1 | P0 |

### FR-7: Attendance Computation & Reporting

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-7.1 | Ratio = `observed_snapshots / total_snapshots`, Present if ≥ 0.85 | P0 |
| FR-7.2 | Batch aggregate query (no N+1) | P0 |
| FR-7.3 | Live heatmap — per-student detection count per schedule | P0 |
| FR-7.4 | Downloadable attendance report (CSV + PDF) per course/date-range | P1 |
| FR-7.5 | Dashboard analytics — attendance trends, anomaly flags | P1 |
| FR-7.6 | Email/webhook notifications for chronic absentees (< 75% threshold) | P2 |

### FR-8: Real-Time Attendance Feed

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-8.1 | SSE endpoint streaming live detection events to dashboard | P0 |
| FR-8.2 | WebSocket channel for device control (existing V1 pattern) | P0 |
| FR-8.3 | Dashboard auto-refreshes attendance grid on new detections | P0 |

### FR-9: React Dashboard (replaces Streamlit for production)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-9.1 | Login/register page | P0 |
| FR-9.2 | Admin dashboard — user management, system stats, audit log viewer | P0 |
| FR-9.3 | Instructor view — course list, enrollment, real-time attendance | P0 |
| FR-9.4 | Student view — personal attendance records, profile | P0 |
| FR-9.5 | Enrollment wizard — guided multi-pose photo capture | P1 |
| FR-9.6 | Dark mode, responsive design (mobile-friendly) | P0 |
| FR-9.7 | Streamlit debug UI preserved for development/testing only | P1 |

### FR-10: Calibration (carried from V1)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-10.1 | Auto-log calibration scores during template tests | P0 |
| FR-10.2 | CSV download for offline analysis | P0 |
| FR-10.3 | `calibrate_threshold` script for optimal thresholds | P0 |

---

## 3. Non-Functional Requirements

### NFR-1: Privacy

| ID | Requirement |
|----|-------------|
| NFR-1.1 | Raw images NEVER persisted to disk — processed in RAM, discarded |
| NFR-1.2 | Only 512-d float embeddings stored (pgvector) |
| NFR-1.3 | Debug frames in bounded in-memory ring buffer only |
| NFR-1.4 | GDPR-aligned: student can request data export/deletion |

### NFR-2: Security

| ID | Requirement |
|----|-------------|
| NFR-2.1 | HMAC-SHA256 payload signing on all device→server uploads |
| NFR-2.2 | Nonce-based replay protection (60s TTL, single-use, Redis-backed) |
| NFR-2.3 | Device secrets hashed with bcrypt |
| NFR-2.4 | Per-device rate limiting on `/ingest` (configurable, default 30s) |
| NFR-2.5 | All passwords Argon2id hashed |
| NFR-2.6 | CORS restricted to known origins |
| NFR-2.7 | OWASP Top 10 hardened |
| NFR-2.8 | API versioning (`/api/v1/`) for backward compatibility |

### NFR-3: Performance

| ID | Requirement |
|----|-------------|
| NFR-3.1 | Single snapshot processing < 5s on CPU for 30-face classroom |
| NFR-3.2 | API response latency < 200ms for CRUD (p95) |
| NFR-3.3 | pgvector HNSW search < 50ms for 10,000 embeddings |
| NFR-3.4 | Dashboard initial load < 2s (code-split + lazy load) |
| NFR-3.5 | All features toggle-able via config flags |

### NFR-4: Scalability

| ID | Requirement |
|----|-------------|
| NFR-4.1 | Horizontal scaling via Docker replicas (Celery workers) |
| NFR-4.2 | Redis for nonce store, session cache, Celery broker |
| NFR-4.3 | PostgreSQL + pgvector for all persistent data |
| NFR-4.4 | Target: 50 concurrent classrooms, 2000 students |

### NFR-5: Observability

| ID | Requirement |
|----|-------------|
| NFR-5.1 | Structured JSON logging (Python `structlog`) |
| NFR-5.2 | Health check endpoint (`/health`) with dependency status |
| NFR-5.3 | Request tracing via correlation IDs |

---

## 4. Data Model (V2)

### Core Tables

| Table | Key Columns | Notes |
|-------|-------------|-------|
| `users` | id, email, password_hash, role, is_active, created_at | NEW — auth system |
| `students` | id, user_id (FK), name, department, enrollment_year, is_enrolled | MODIFIED — linked to user |
| `student_embeddings` | id, student_id, pose_label, resolution, model_name, embedding (VECTOR 512) | MODIFIED — pgvector native |
| `rooms` | id, room_name, capacity | MODIFIED — added capacity |
| `devices` | id, room_id, secret_key_hash, type (camera/laptop), rtsp_url | MODIFIED — RTSP support |
| `courses` | id, code, name, instructor_id (FK→users), department | NEW — normalized from schedules |
| `schedules` | id, course_id, room_id, start_time, end_time, days_of_week | MODIFIED — links to course |
| `snapshots` | id, schedule_id, timestamp, expected_count | Unchanged |
| `detections` | id, snapshot_id, student_id, confidence, camera_id | Unchanged |
| `audit_logs` | id, user_id, action, resource, details (JSONB), created_at | NEW |
| `refresh_tokens` | id, user_id, token_hash, expires_at, revoked | NEW |

---

## 5. API Surface (V2)

All endpoints under `/api/v1/` prefix.

### Authentication
| Method | Path | Auth | Role |
|--------|------|------|------|
| POST | `/auth/register` | None | — |
| POST | `/auth/login` | None | — |
| POST | `/auth/refresh` | Refresh Token | — |
| POST | `/auth/logout` | JWT | Any |
| POST | `/auth/forgot-password` | None | — |
| POST | `/auth/reset-password` | Token | — |

### Users (Admin)
| Method | Path | Auth | Role |
|--------|------|------|------|
| GET | `/users` | JWT | admin |
| PATCH | `/users/{id}` | JWT | admin |
| DELETE | `/users/{id}` | JWT | admin |

### Students
| Method | Path | Auth | Role |
|--------|------|------|------|
| POST | `/students/enroll` | JWT | admin, instructor |
| POST | `/students/enroll-from-images` | JWT | admin, instructor |
| POST | `/students/enroll-multi-pose` | JWT | admin, instructor |
| GET | `/students` | JWT | admin, instructor |
| GET | `/students/{id}` | JWT | admin, instructor, self |

### Courses & Schedules
| Method | Path | Auth | Role |
|--------|------|------|------|
| POST | `/courses` | JWT | admin |
| GET | `/courses` | JWT | admin, instructor |
| POST | `/schedules` | JWT | admin, instructor |
| GET | `/schedules` | JWT | admin, instructor |

### Rooms & Devices
| Method | Path | Auth | Role |
|--------|------|------|------|
| POST | `/rooms` | JWT | admin |
| POST | `/devices` | JWT | admin |
| GET | `/devices` | JWT | admin |

### Ingest (Device Auth)
| Method | Path | Auth | Role |
|--------|------|------|------|
| POST | `/ingest` | HMAC | device |
| POST | `/ingest/clip` | HMAC | device |

### Attendance & Reporting
| Method | Path | Auth | Role |
|--------|------|------|------|
| GET | `/attendance/{schedule_id}` | JWT | admin, instructor |
| GET | `/attendance/student/{id}` | JWT | admin, instructor, self |
| GET | `/attendance/report` | JWT | admin, instructor |
| GET | `/attendance/stream` | JWT (SSE) | admin, instructor |

### System
| Method | Path | Auth | Role |
|--------|------|------|------|
| GET | `/health` | None | — |
| GET | `/ai/status` | JWT | admin |
| GET | `/audit-logs` | JWT | admin |
| POST | `/orchestrator/trigger` | JWT | admin |

---

## 6. Acceptance Criteria

| # | Criterion |
|---|-----------|
| AC-1 | A new user can register, verify, and log in |
| AC-2 | Admin can CRUD users, rooms, devices, courses, schedules |
| AC-3 | Instructor can enroll students and view attendance for own courses only |
| AC-4 | Student can view only their own attendance records |
| AC-5 | Device can upload snapshots/clips with HMAC — rejected without valid HMAC |
| AC-6 | Face detection + recognition pipeline produces correct attendance from uploaded images |
| AC-7 | Dashboard shows real-time attendance updates via SSE |
| AC-8 | CSV and PDF reports downloadable for any date range |
| AC-9 | All API endpoints return proper 401/403 for unauthorized/forbidden access |
| AC-10 | System handles 50 concurrent classrooms without degradation |

---

## 7. Out of Scope (V2)

- Mobile app (defer to V3)
- Multi-tenant / multi-institution (DB schema prepped, routing deferred)
- TensorRT / Triton Inference Server (requires GPU server)
- Doorway tracking (secondary camera needed)
- OAuth2 social login (P2, deferred)
