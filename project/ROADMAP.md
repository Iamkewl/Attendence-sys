# Roadmap: Workspace Cleanup, V2 Parity, Legacy Removal

Date: 2026-04-21  
Status: Approved execution plan  
Owner: Engineering

## Scope

This roadmap intentionally contains only three parts:

1. Clear and rationalize the workspace.
2. Ensure everything documented in SYSTEM_OVERVIEW is implemented and validated.
3. Remove legacy components that were replaced by post-improvement architecture.

All previous roadmap content is superseded by this file.

## Global Execution Rules

1. Default action for non-critical artifacts is remove, not keep.
2. Keep only one canonical document per topic.
3. Do not delete anything without a quick dependency check and rollback path.
4. Ship each part as a separate commit so rollback is clean.
5. Use objective checks at the end of each part before moving to the next.

---

## Part 1: Workspace Cleanup and Documentation Consolidation

### 1.1 Goals

1. Remove duplicated folders, stale artifacts, empty directories, and prompt leftovers.
2. Reduce root-level clutter and make navigation obvious.
3. Consolidate scattered documentation into one reference document, while preserving truly critical standalone docs.

### 1.2 Cleanup Policy

1. Keep at repository root only the canonical docs:
   - README.md
   - SYSTEM_OVERVIEW.md
   - REQUIREMENTS.md
   - ARCHITECTURE.md
   - ROADMAP.md
2. Consolidate non-critical overlapping docs into one document:
   - Target file: docs/PROJECT_REFERENCE.md
   - Merge from: FIRST_TIME_USER_GUIDE.md, FACE_RECOGNITION_SYSTEM_OUTLINE.md, FACIAL_RECOGNITION_STRATEGIC_ANALYSIS_2026.md
3. Keep critical specialized docs separate:
   - SECURITY_REVIEW_2026-04-08.md
   - docs/runbooks/*

### 1.3 Concrete Workspace Actions

1. Remove structural garbage:
   - Delete nested Attendence-sys/ duplicate skeleton.
   - Delete empty .agents/ and .state/ directories if still empty.
2. Remove legacy root artifacts:
   - Delete yolov8n.pt.
   - Delete main_agent.json if no active toolchain depends on it.
3. Remove prompt noise from root:
   - Delete AUDIT_FIX_PROMPTS.md.
   - Delete PHASE_PROMPTS.md.
   - Replace phase0.md through phase8.md with one docs/PHASE_STATUS.md summary, then delete phase*.md.
4. Normalize agent instruction structure:
   - Choose one canonical skills location: .github/skills/.
   - Remove mirrored duplicates under agent/skills/ after verification.
5. Treat generated output as generated:
   - Keep graphify-out/ local for knowledge navigation.
   - Ensure graphify-out/ and model cache paths are ignored by git.

### 1.4 Documentation Consolidation Deliverable

Create docs/PROJECT_REFERENCE.md with these sections:

1. Product Purpose and Deployment Context
2. User Flows and Operational Procedures
3. CV Pipeline Summary and Design Decisions
4. Governance, Security, and Compliance Notes
5. Links to canonical docs and runbooks

### 1.5 Acceptance Criteria for Part 1

1. Root markdown count is reduced to canonical docs only.
2. No duplicate project skeleton folders remain.
3. No empty utility directories remain.
4. One consolidated project reference document exists in docs/.
5. A before/after inventory is recorded in commit message body.

---

## Part 2: SYSTEM_OVERVIEW Implementation Verification and Closure

### 2.1 Goals

1. Convert SYSTEM_OVERVIEW from narrative to a verified implementation checklist.
2. Detect every missing, partial, or untested capability.
3. Close all gaps with evidence-based validation.

### 2.2 Traceability Matrix Build

Build and maintain a sectioned implementation matrix in this roadmap while executing.  
Each item must include: expected behavior, code location, test coverage, runtime verification, status.

Matrix sections:

1. Platform Stack and Runtime Services
   - FastAPI, Celery, PostgreSQL, Redis, frontend, Triton profile
2. API Modules
   - auth, students, attendance, courses, ingest, system, sse, websocket, users
3. AI Pipeline (8 stages)
   - detection, quality, preprocessing, embedding, retrieval, aggregation, decision, output
4. Tracking and Cross-Camera ReID
   - tracker lifecycle, linker, diagnostics
5. Liveness (all tiers in use)
6. Database Models and Migrations
7. Configuration and Runtime Overrides
8. Frontend Page Coverage
9. Security Controls

### 2.2.1 Implementation Matrix (Started 2026-04-21)

Verification commands executed in this pass:

1. `python -m pytest tests/test_auth.py -q --tb=short` -> 4 passed, 4 failed (Redis unavailable at localhost:6379)
2. `python -m pytest tests/test_endpoints.py -k "TestSystemAPI" -q --tb=short` -> 1 passed, 2 failed (Redis unavailable at localhost:6379)
3. `python -m pytest tests/test_units.py -k "TestLivenessTierExtensions or TestTrackerManager or TestCrossCameraLinker" -q --tb=short` -> 6 passed
4. `python -m pytest tests/test_units.py -k "TestHMACAuth or TestOnnxProviderConfiguration or TestVectorSearchFilters" -q --tb=short` -> 15 passed
5. `winget install --id Redis.Redis --exact --accept-package-agreements --accept-source-agreements --silent` -> installed local Redis service
6. `redis-cli -h 127.0.0.1 -p 6379 ping` -> PONG
7. `python -m pytest tests/test_auth.py -q --tb=short` -> 8 passed
8. `python -m pytest tests/test_endpoints.py -k "TestSystemAPI" -q --tb=short` -> 3 passed
9. `python -m pytest tests/test_endpoints.py tests/test_units.py -q --tb=short` -> 48 passed
10. `python -m pytest tests -q --tb=short` -> 56 passed
11. `python -m scripts.smoke_test` -> failed (Celery task registry empty, PostgreSQL unavailable on localhost:55432)
12. `python -c "from backend.core.config import get_settings; from urllib.parse import urlparse; ..."` -> DATABASE_URL target confirmed as `postgresql+asyncpg://localhost:55432/attendance` (sanitized)
13. `docker --version; docker compose up -d postgres; docker compose ps postgres` -> Docker daemon unavailable (cannot connect to `dockerDesktopLinuxEngine`)
14. `python -m scripts.smoke_test` -> Celery fixed/verified (registered_task_count=5), PostgreSQL still unavailable
15. `Set-Location frontend; if (Test-Path node_modules) { npm run build } else { npm install; npm run build }` -> Vite production build succeeded
16. `Get-Service com.docker.service | Select-Object Name,Status,StartType` -> Docker Desktop service is `Stopped` (`Manual`)
17. `Start-Service com.docker.service; Get-Service com.docker.service | Select-Object Name,Status,StartType` -> start failed (insufficient permission)
18. `Set-Location ..; python -m pytest tests -q --tb=short` -> 56 passed (post-fix regression check)
19. `python -m pytest tests/test_endpoints.py -q --tb=short` -> 15 passed (includes ingest snapshot/clip dispatch tests)
20. `python -m pytest tests -q --tb=short` -> 58 passed
21. `python -m pytest tests/test_endpoints.py -q --tb=short` -> 18 passed (includes realtime SSE/WebSocket endpoint behavior tests)
22. `python -m pytest tests -q --tb=short` -> 61 passed
23. `python -m alembic history --verbose` -> revision chain verified (`20260408_01` -> `20260411_01` head)
24. `DATABASE_URL=sqlite+aiosqlite:///./alembic_verify.db; python -m alembic upgrade head` -> failed with `NoSuchTableError: student_embeddings` (migrations assume baseline tables already exist)
25. Browser route sweep (Playwright, unauthenticated) over `/, /students, /attendance, /live, /testing, /users, /settings, /login` -> protected routes redirect to `/login` as expected
26. Browser route sweep with synthetic auth token (Playwright) over protected routes -> all routes render app shell and pages; per-route API request emissions observed (dashboard, students, attendance, live, testing, users, settings)
27. `python -m pytest tests/test_units.py -q --tb=short` -> 38 passed (includes `TestAIPipelineScoringDecision`)
28. `python -m pytest tests -q --tb=short` -> 64 passed
29. `docker version; docker info` -> Docker daemon reachable and healthy
30. `docker compose up -d postgres redis` -> failed because port `6379` is already allocated by host Redis service
31. `docker compose up -d postgres` -> PostgreSQL container started and reached healthy state
32. `docker compose ps; docker logs --tail 80 project-postgres-1` -> PostgreSQL runtime ready and accepting connections
33. `python -m scripts.smoke_test` -> all checks passed (`fastapi_health`, `celery_tasks`, `redis`, `postgres`, `ai_pipeline`)
34. `python -m alembic upgrade head; python -m alembic current` -> live PostgreSQL upgraded to `20260411_01 (head)`
35. `python -c "... inspect(...).get_table_names() ..."` -> live schema reports 14 tables (users/students/courses/attendance/governance + embeddings)
36. `python -m pytest tests -q --tb=short` -> 64 passed (post-runtime verification regression)
37. `python -m pytest tests/test_endpoints.py -q --tb=short` -> 2 failed (`TestStudentsAPI::test_guided_burst_enrollment_to_verification_pass`, `TestStudentsAPI::test_enroll_student_from_images`) after LVFace-only settings enforcement
38. `triton_models/**` repository inventory -> all version directories contain `.gitkeep` placeholders; no `model.onnx`/`model.plan` artifacts present
39. Placeholder/legacy keyword sweep across runtime paths (`backend/**`, `frontend/src/**`, `tests/**`) -> active ArcFace/AdaFace and YOLOv8 codepaths remain in pipeline/worker/API/UI contracts
40. `models/**/*lvface*` inventory -> no local LVFace artifact present under `models/` for Triton engine build input
41. `docker compose up -d triton; docker compose ps triton; docker compose logs --tail 120 triton; GET /v2/health/live,/ready` -> container entered restart loop (`Restarting (1)`), all configured models were `UNAVAILABLE` due to missing `/models/*/1/model.plan`, and health endpoints were unreachable
42. `python -m pytest tests/test_endpoints.py -k "guided_burst_enrollment_to_verification_pass or enroll_student_from_images" -q --tb=short` -> 2 passed (LVFace-only students enrollment/verification regression fixed)
43. `python -m pytest tests/test_endpoints.py -q --tb=short; python -m pytest tests -q --tb=short` -> 18 passed; 64 passed
44. `cd frontend; npm run build` -> Vite production build succeeded after Students UI payload/metric contract update
45. `Select-String -Path backend/api/v1/students.py,frontend/src/api/client.js,frontend/src/pages/StudentsPage.jsx,tests/test_endpoints.py -Pattern 'use_adaface|active_arcface_embeddings|total_arcface_embeddings|extract_embedding_adaface'` -> `NO_MATCHES`
46. `python -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` -> graph rebuilt (1346 nodes, 3727 edges, 80 communities)

#### A) Platform Stack and Runtime Services

| Item | Expected behavior | Code location | Test coverage | Runtime verification | Status |
|---|---|---|---|---|---|
| FastAPI app | App boots and exposes health/ready and API routes | backend/main.py, backend/api/v1/system.py | tests/test_auth.py (TestHealthEndpoints) | Smoke check `/health` passed in live startup pass | implemented_and_verified |
| Celery workers | CV tasks are registered and runnable from worker queue | backend/workers/celery_app.py, backend/workers/cv_tasks.py | Regression suite green after worker wiring fix (`python -m pytest tests -q`) | Smoke check verified worker readiness and task registration (`registered_task_count=5`) | implemented_and_verified |
| PostgreSQL | Async ORM and model persistence are available for API and workers | backend/db/session.py, backend/models/* | Indirect via endpoint tests | Docker PostgreSQL container reached healthy state and smoke check confirmed live `SELECT 1` connectivity | implemented_and_verified |
| Redis | Rate limiting, nonce/session caching, broker functions available | backend/services/redis_service.py | Auth/system tests exercise Redis-dependent paths | Local service running and reachable (PONG); auth/system subsets pass | implemented_and_verified |
| Frontend service | React/Vite UI starts and routes pages for operator workflows | frontend/src/App.jsx, frontend/vite.config.js | No frontend automated tests in this pass | Production build succeeds (`npm run build`) and Playwright runtime route sweep verifies protected-route gating and authenticated-shell rendering | implemented_and_verified |
| Triton profile | Triton service and fallback-capable client integration exist | docker-compose.yml, backend/services/triton_client.py, triton_models/* | No direct Triton tests in this pass | Live startup attempt shows hard failure: Triton enters restart loop because all configured models are missing `model.plan` artifacts (`UNAVAILABLE`), so `/v2/health/live` and `/v2/health/ready` are unreachable; fallback path remains observable (`triton_enabled=false`, `triton_available=false`) | partial |

#### B) API Modules

| Module | Expected behavior | Code location | Test coverage | Runtime verification | Status |
|---|---|---|---|---|---|
| auth | Register/login/refresh/logout with JWT and refresh rotation | backend/api/v1/auth.py | tests/test_auth.py (TestAuthFlow) | 8 passed after local Redis setup | implemented_and_verified |
| students | Student CRUD, enrollment, lifecycle, verification routes | backend/api/v1/students.py | tests/test_endpoints.py (TestStudentsAPI) | LVFace-only enrollment/verification regressions fixed (`2 passed` targeted), endpoint suite green (`18 passed`), full suite green (`64 passed`) | implemented_and_verified |
| attendance | Schedule attendance reporting and export endpoints | backend/api/v1/attendance.py | tests/test_endpoints.py (TestAttendanceAPI), tests/test_endpoints.py (TestAdminAndReportingAPI::test_attendance_report_and_csv_export) | Executed in latest full suite pass (61 passed) | implemented_and_verified |
| courses | Course and schedule management routes | backend/api/v1/courses.py | tests/test_endpoints.py (TestAdminAndReportingAPI::test_courses_crud) | Executed in latest full suite pass (61 passed) | implemented_and_verified |
| ingest | Device-authenticated snapshot/clip ingestion | backend/api/v1/ingest.py | tests/test_units.py (TestHMACAuth), tests/test_endpoints.py (TestIngestAPI::test_ingest_snapshot_dispatches_task), tests/test_endpoints.py (TestIngestAPI::test_ingest_clip_dispatches_task) | Signed ingest request dispatch path verified in endpoint suite (15 passed) | implemented_and_verified |
| system | Health, diagnostics, settings, governance endpoints | backend/api/v1/system.py | tests/test_endpoints.py (TestSystemAPI) | 3 passed after local Redis setup | implemented_and_verified |
| sse | Server-sent event stream endpoint | backend/api/v1/sse.py | tests/test_endpoints.py (TestAdminAndReportingAPI::test_sse_status_and_auth_failure_paths), tests/test_endpoints.py (TestRealtimeAPI::test_attendance_sse_stream_emits_detection_event) | Status/auth plus event stream emission path verified in endpoint suite (18 passed) | implemented_and_verified |
| websocket | Device orchestration over WebSocket | backend/api/v1/websocket.py | tests/test_units.py (TestAttendanceBroadcaster), tests/test_endpoints.py (TestRealtimeAPI::test_attendance_ws_rejects_invalid_token), tests/test_endpoints.py (TestRealtimeAPI::test_attendance_ws_accepts_admin_and_unsubscribes_on_disconnect) | Broadcaster and endpoint auth/handshake/disconnect lifecycle verified in tests | implemented_and_verified |
| users | User CRUD and role-managed administration routes | backend/api/v1/users.py | tests/test_endpoints.py (TestAdminAndReportingAPI::test_users_crud_and_rbac) | Executed in latest full suite pass (61 passed) | implemented_and_verified |

#### C) AI Pipeline (8 Stages)

| Stage | Expected behavior | Code location | Test coverage | Runtime verification | Status |
|---|---|---|---|---|---|
| Detection | Dual-pass detector flow and face-box handling | backend/services/ai_pipeline.py | tests/test_units.py (TestEnrollmentFaceSelection) | No end-to-end detector run in this pass | implemented_not_verified |
| Quality | Blur/size/area quality gating and scoring | backend/services/ai_pipeline.py | Indirect coverage in unit and endpoint suites | No runtime quality benchmark in this pass | implemented_not_verified |
| Preprocessing | White balance, gamma, CLAHE, restoration/SR policy path | backend/services/preprocessing.py, backend/services/face_restoration.py | tests/test_units.py (TestPreprocessingPolicy) | Preprocessing policy behavior validated in direct unit coverage (latest suite 64 passed) | implemented_and_verified |
| Embedding | ArcFace/AdaFace/LVFace extraction with provider routing | backend/services/ai_pipeline.py | tests/test_units.py (TestOnnxProviderConfiguration) | Unit provider tests passed in 15-pass subset | implemented_not_verified |
| Retrieval | Vector retrieval filters and backend selection support | backend/db/vector.py, backend/services/ai_pipeline.py | tests/test_units.py (TestVectorSearchFilters) | Vector filter SQL/parameter behavior validated in direct unit tests | implemented_and_verified |
| Aggregation | Per-student score aggregation with weighting | backend/services/ai_pipeline.py | tests/test_units.py (TestAIPipelineScoringDecision::test_score_per_student_uses_max_template_score_per_identity) | Aggregation logic validated with explicit per-student score selection test | implemented_and_verified |
| Decision | Strict/relaxed threshold and margin acceptance logic | backend/services/ai_pipeline.py | tests/test_units.py (TestAIPipelineScoringDecision::test_match_decision_honors_strict_relaxed_margin_and_lvface_thresholds) | Decision threshold/margin behavior validated with explicit unit coverage | implemented_and_verified |
| Output | Persisted detections/events with deduplication and publish | backend/workers/cv_tasks.py | tests/test_units.py (TestAIPipelineScoringDecision::test_dedupe_by_student_keeps_highest_confidence_match), endpoint/system suites (indirect) | Dedupe logic verified; worker persistence + Redis publish still need live-run verification | implemented_not_verified |

#### D) Tracking and Cross-Camera ReID

| Item | Expected behavior | Code location | Test coverage | Runtime verification | Status |
|---|---|---|---|---|---|
| Tracker lifecycle | Track creation/update/stale cleanup and state transitions | backend/services/tracker.py, backend/workers/cv_tasks.py | tests/test_units.py (TestTrackerManager) | Tracker update/confirmation/cleanup behavior verified in direct unit coverage | implemented_and_verified |
| Cross-camera linker | Candidate linking using ReID plus temporal priors | backend/services/cross_camera.py, backend/services/reid.py | tests/test_units.py (TestCrossCameraLinker) | Cross-camera candidate linking behavior verified in direct unit coverage | implemented_and_verified |
| Diagnostics | Track and cross-camera diagnostics exposed for operators | backend/api/v1/system.py | tests/test_endpoints.py (TestSystemAPI) | Covered in passing system subset (3 passed) | implemented_and_verified |

#### E) Liveness (All Tiers)

| Item | Expected behavior | Code location | Test coverage | Runtime verification | Status |
|---|---|---|---|---|---|
| Tiered liveness cascade | Motion, optical flow, CNN anti-spoof, rPPG, flash checks | backend/services/liveness.py, backend/workers/cv_tasks.py | tests/test_units.py (TestLivenessTierExtensions) | Tier extension behavior validated in direct unit tests (rPPG + flash paths) | implemented_and_verified |

#### F) Database Models and Migrations

| Item | Expected behavior | Code location | Test coverage | Runtime verification | Status |
|---|---|---|---|---|---|
| Model coverage | Core entities for users/students/courses/attendance/governance exist | backend/models/* | Indirect via unit and endpoint tests | Live PostgreSQL schema inspection after migration reports 14 core tables, including governance and embeddings tables | implemented_and_verified |
| Migration coverage | Alembic revisions capture schema evolution and governance changes | alembic/versions/* | Revision chain validated (`alembic history --verbose`) | `alembic upgrade head` succeeds on live PostgreSQL and `alembic current` confirms `20260411_01 (head)`; prior sqlite failure remains a disposable-baseline caveat | implemented_and_verified |

#### G) Configuration and Runtime Overrides

| Item | Expected behavior | Code location | Test coverage | Runtime verification | Status |
|---|---|---|---|---|---|
| Base config flags | Feature flags and defaults exist for all major subsystems | backend/core/config.py | tests/test_units.py (TestOnnxProviderConfiguration) | Config defaults across phase flags validated in dedicated unit suite | implemented_and_verified |
| Runtime override path | System settings are loadable and mutable via system module | backend/data/system_settings.json, backend/api/v1/system.py | tests/test_endpoints.py (TestSystemAPI) | Verified in passing system subset after Redis setup | implemented_and_verified |

#### H) Frontend Page Coverage

| Item | Expected behavior | Code location | Test coverage | Runtime verification | Status |
|---|---|---|---|---|---|
| Page inventory | Core operator pages exist and are wired in app shell | frontend/src/pages/*, frontend/src/App.jsx | Browser runtime sweep across all declared routes in `App.jsx` | Playwright verification confirms protected-route redirects and authenticated-shell page rendering for all core routes | implemented_and_verified |
| API integration layer | Frontend API client supports operational actions and diagnostics | frontend/src/api/client.js | Browser route-driven request tracing | Playwright trace observed expected `/api/v1/*` calls per page (dashboard/students/attendance/live/testing/users/settings), confirming route-to-client wiring | implemented_and_verified |

#### I) Security Controls

| Item | Expected behavior | Code location | Test coverage | Runtime verification | Status |
|---|---|---|---|---|---|
| JWT + password security | JWT auth and Argon2 password hashing paths enforce secure auth | backend/core/security.py, backend/api/v1/auth.py | tests/test_auth.py (TestAuthFlow) | Verified in passing auth subset (8 passed) | implemented_and_verified |
| RBAC enforcement | Role-based guards protect privileged routes | backend/api/deps.py, backend/api/v1/users.py, backend/api/v1/system.py | tests/test_endpoints.py (TestAdminAndReportingAPI::test_users_crud_and_rbac), tests/test_endpoints.py (TestAttendanceAPI) | Verified in latest full suite pass (61 passed) | implemented_and_verified |
| HMAC ingest auth | Device request signing and verification guard ingest path | backend/services/security.py, backend/api/v1/ingest.py | tests/test_units.py (TestHMACAuth), tests/test_endpoints.py (TestIngestAPI) | HMAC unit coverage plus signed ingest endpoint dispatch coverage verified | implemented_and_verified |
| Rate limiting and nonce anti-replay | Redis-backed rate limit and nonce/session controls are active | backend/services/redis_service.py, backend/api/v1/auth.py, backend/api/v1/ingest.py | Auth/system tests hit these paths | Verified after local Redis setup and passing auth/system subsets | implemented_and_verified |

### 2.2.2 Part 3 Readiness Snapshot (2026-04-24)

Part 2 verification is now narrowed to a small set of runtime gaps; Docker/PostgreSQL/migration coverage has been closed in this pass.

Remaining `implemented_not_verified` rows before declaring Part 2 complete:

1. Platform: Triton profile live startup/inference verification (startup attempted; currently blocked by missing `model.plan` artifacts and restart-looping server).
2. AI pipeline: Detection stage end-to-end runtime evidence.
3. AI pipeline: Quality stage runtime benchmark evidence.
4. AI pipeline: Embedding stage runtime extraction evidence under target LVFace-primary policy.
5. AI pipeline: Output stage live persistence + Redis publish verification.

Legacy-removal execution (Part 3) is now primarily blocked by backend runtime/config references (`ai_pipeline`, `cv_tasks`, and model defaults) to deprecated model families (`arcface|adaface|insightface|yolov8`); students enrollment API/UI/test contracts have been migrated to LVFace-only in this pass.

Conclusion: Triton runtime gate is now conclusively blocked by missing deployable model artifacts (not by lack of execution). Next execution should focus on provisioning/building required model plans, then rerunning Triton health/inference verification before Part 3 completion claims.

### 2.2.3 Deep Placeholder Inventory (2026-04-24)

The following placeholder and architecture-alignment gaps are still present relative to the new SYSTEM_OVERVIEW target stack.

| Area | Evidence location | Placeholder or incomplete state | Impact on roadmap gates | Current state |
|---|---|---|---|---|
| Triton model artifacts | triton_models/README.md, triton_models/*/1/.gitkeep, scripts/build_triton_engines.ps1 | Versioned Triton model folders are placeholders only; expected `model.onnx`/`model.plan` artifacts are absent in-repo | Blocks Part 2 Triton runtime verification and any live Triton inference evidence | blocking |
| Triton live startup health | docker compose triton runtime check (verification command #41) | Container restart loop occurs because every configured model is `UNAVAILABLE` (missing `/models/*/1/model.plan`), leaving health endpoints unreachable | Confirms Triton gate is an active runtime blocker, not just an untested path | blocking |
| LVFace source artifact for engine build | models/ (inventory), scripts/build_triton_engines.ps1 (`models/lvface_base.onnx`) | Build script expects LVFace ONNX input under `models/`, but local inventory does not contain LVFace model file | Blocks Triton LVFace profile materialization without external artifact provisioning | blocking |
| Runtime Triton activation defaults | docker-compose.yml (`ENABLE_TRITON=false` for app/cv-worker), backend/core/config.py (`enable_triton=false`) | Triton integration exists but is default-disabled in both config and compose runtime env | Keeps runtime in fallback-only mode unless explicitly overridden | partial |
| Detector architecture alignment | backend/core/config.py (YOLOv8 defaults, `enable_yolov12=false`), backend/services/ai_pipeline.py (YOLOv8 default fallback) | YOLOv12 path is implemented but not the default runtime path | Part 2 detection runtime evidence remains misaligned with SYSTEM_OVERVIEW default architecture | partial |
| Embedding architecture alignment | backend/core/config.py (`enable_lvface=false`, `enable_adaface=true`), backend/services/ai_pipeline.py, backend/workers/cv_tasks.py | Multi-model ArcFace/AdaFace/LVFace fusion paths still active in pipeline and worker logic | Part 2 embedding/output validation remains mixed-path; Part 3 legacy removal still pending | partial |
| Students API contract alignment | backend/api/v1/students.py (LVFace-only enrollment/test scoring and metrics fields) | Enrollment/verification API no longer accepts `use_adaface`, no longer emits ArcFace-centric counters, and now uses LVFace-only probe/template scoring | Closes prior endpoint regression and removes one major Part 3 drift source | implemented_and_verified |
| Frontend enrollment contract alignment | frontend/src/pages/StudentsPage.jsx, frontend/src/api/client.js | UI and payloads no longer send AdaFace enrollment toggles and now consume LVFace-neutral counter fields (`active_embeddings`, `total_embeddings`) | User-facing enrollment contract now aligned with LVFace-only backend | implemented_and_verified |
| Test contract alignment | tests/test_endpoints.py | Legacy enrollment payload/assertion assumptions replaced with LVFace-only assertions and monkeypatch targets | Regression gate turned green (`2 passed` targeted, `18 passed` endpoint suite, `64 passed` full suite) | implemented_and_verified |

### 2.3 Gap Triage Rules

1. Status values:
   - implemented_and_verified
   - implemented_not_verified
   - partial
   - missing
2. Prioritize in this order:
   - P0: missing core runtime behavior
   - P1: partial core behavior
   - P2: diagnostics and optimization parity

### 2.4 Validation Workflow

1. Static evidence pass:
   - Confirm code paths and config flags exist.
2. Test evidence pass:
   - Confirm test cases exist for each critical SYSTEM_OVERVIEW claim.
3. Runtime evidence pass:
   - Confirm behavior in live run path using API and worker checks.
4. Performance evidence pass:
   - Confirm throughput/latency claims that are marked production-relevant.

### 2.5 Acceptance Criteria for Part 2

1. Every SYSTEM_OVERVIEW capability has a matrix row with status and evidence.
2. No row remains partial or missing for core production behaviors.
3. Verification artifacts are reproducible from commands and tests in repository.

---

## Part 3: Legacy Removal After V2 Improvements

### 3.1 Goals

1. Remove all superseded model families, configs, scripts, and dead code branches.
2. Ensure no runtime path can silently fall back to deprecated components.
3. Keep migrations and compatibility only where strictly required.

### 3.2 Legacy Scope to Remove

1. Recognition legacy family:
   - arcface
   - adaface
   - insightface naming and provider remnants
2. Detection legacy family:
   - yolov8 runtime/model artifacts replaced by YOLOv12 path
3. Legacy weights, flags, and fusion settings no longer valid under LVFace-primary architecture

### 3.3 Concrete Removal Actions

1. Code and config cleanup:
   - Remove deprecated enum values, request fields, settings keys, and branches.
   - Remove deprecated fallback loading paths.
2. Artifact cleanup:
   - Remove triton_models/arcface and triton_models/adaface if no active inference profile requires them.
   - Remove root and models artifacts that are legacy-only.
3. Data cleanup:
   - Add migration or data fix path for historical model_name values where needed.
4. Script and test cleanup:
   - Remove legacy-only script logic.
   - Update tests to validate LVFace-only assumptions.
   - Completed (2026-04-24): replaced legacy enrollment payload fields (`use_adaface`, ArcFace-centric counters) with LVFace-only API and UI contracts.
5. Guardrail checks:
   - Add CI grep guard that fails on new legacy keyword reintroduction in active code paths.

### 3.4 Final Verification Gates

1. Zero matches in active backend, frontend, and tests for deprecated legacy keywords, excluding intentional migration history.
2. End-to-end enrollment and recognition pass with LVFace-only production path.
3. No startup warnings for missing legacy models.
4. Docker and worker startup clean under the post-removal configuration.

### 3.5 Acceptance Criteria for Part 3

1. Legacy branches are removed, not just disabled.
2. Runtime behavior is unchanged for supported V2 features.
3. Regression tests pass after removal.

---

## Execution Sequence

1. Execute Part 1 completely, including documentation consolidation and folder cleanup.
2. Execute Part 2 fully and close all core parity gaps from SYSTEM_OVERVIEW.
3. Execute Part 3 only after Part 2 parity gates pass.

No parallel shortcuts across parts are allowed. Each part has hard acceptance gates.

## Completion Definition

This roadmap is complete when:

1. Workspace is materially cleaner and navigable.
2. SYSTEM_OVERVIEW is fully verified against implemented behavior.
3. Legacy architecture remnants are removed and cannot regress silently.
