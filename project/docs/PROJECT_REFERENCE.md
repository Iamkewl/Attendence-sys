# Project Reference

Date: 2026-04-21
Purpose: Consolidated project reference for onboarding, operations, CV design, and governance context.

## 1. Product Purpose and Deployment Context

Attendance System V2 is an AI-assisted classroom attendance platform built for low-friction, high-trust attendance capture using facial recognition and liveness checks.

Primary outcomes:
- Automate attendance capture across scheduled classes.
- Support real-time monitoring and post-session reporting.
- Preserve operator visibility with diagnostics, auditability, and rollback controls.

Target deployment profiles:
- Local development on Windows/Linux/macOS.
- Docker Compose deployment for app, workers, PostgreSQL, and Redis.
- Optional GPU-accelerated deployment (for example NVIDIA A10) with Triton-enabled inference paths.

Core stack at a glance:
- Backend: FastAPI, Celery, SQLAlchemy, Redis.
- Data: PostgreSQL plus pgvector.
- Frontend: React and Vite.
- CV/ML: YOLO-family detection, ArcFace/AdaFace/LVFace embeddings, ONNX Runtime, optional Triton.

Default local endpoints:
- API: http://127.0.0.1:8000
- API Docs: http://127.0.0.1:8000/docs
- Frontend: http://127.0.0.1:5173

## 2. User Flows and Operational Procedures

### 2.1 First Run (Recommended)

1. Start infrastructure services.
2. Apply migrations.
3. Optionally seed demo data.
4. Start backend service.
5. Start frontend development server.
6. Sign in with admin credentials and verify dashboard health.

### 2.2 Day-to-Day Operator Flows

Dashboard:
- Review system readiness and top-level attendance metrics.
- Confirm service health before active class windows.

Students:
- Create and manage student records.
- Enroll with guided burst capture or uploaded images.
- Review enrollment diagnostics and rejection reasons.
- Run post-enrollment verification with a fresh probe image.

Attendance:
- Filter by schedule/course context.
- Review presence ratios and detection outcomes.
- Export CSV reports for operations and academic audit.

Live Feed:
- Monitor incoming attendance and device events in real time.
- Use for incident checks during active sessions.

Testing Lab:
- Run group-photo recognition tests.
- Compare expected roster vs recognized outcomes.
- Inspect false positives, missed students, and annotated image output.

Users:
- Admin role lifecycle management (create, update, deactivate/delete).

Settings:
- Tune runtime behavior with bounded, auditable changes.
- Review revision history and roll back when needed.

### 2.3 Operational Patterns

Recommended routine:
1. Confirm infra and API health.
2. Verify ingestion and live events during first class window.
3. Spot-check enrollment quality for newly added students.
4. Export attendance summaries at the end of each day/session.
5. Review governance indicators (fairness, retention, drift) on schedule.

## 3. CV Pipeline Summary and Design Decisions

### 3.1 Snapshot Recognition Flow

1. Device upload arrives with nonce and HMAC signature.
2. API validates authenticity and replay constraints.
3. Celery worker processes frame.
4. Detection runs (dual-pass where enabled).
5. Face quality gating and preprocessing are applied.
6. Embeddings are extracted (ArcFace/AdaFace/LVFace based on flags).
7. Matching/fusion and threshold logic produce final identity decision.
8. Detections are stored and events are published.

### 3.2 Key Design Decisions

Detection strategy:
- SAHI-assisted dual-pass detection is used to improve distant/small-face recovery.
- YOLO-family routing is feature-flagged to allow controlled migrations.

Embedding strategy:
- Multi-model support enables resilience across varying quality and pose conditions.
- Model-specific confidence and thresholds are tunable without hard redeploy.

Matching strategy:
- Current stack supports in-process matching and ANN-oriented pathways.
- Lifecycle-aware template selection is used (active, backup, quarantined).

Temporal and multi-camera strategy:
- Tracking-first logic reduces repeated recognition and supports continuity.
- Cross-camera linking adds handoff continuity where topology and priors allow.

Anti-spoofing strategy:
- Cascaded liveness tiers (from inexpensive checks to stronger checks) optimize cost vs protection.

Restoration strategy:
- Face restoration is policy-gated and identity-preservation checked before acceptance.

### 3.3 Runtime Governance of CV Behavior

Most major CV behaviors are controlled by feature flags and thresholds in settings/config layers to support:
- Safe rollout.
- Fast rollback.
- Environment-specific tuning.
- Promotion gates based on evidence.

## 4. Governance, Security, and Compliance Notes

### 4.1 Security Controls

Implemented controls include:
- JWT-based auth with refresh handling.
- RBAC authorization boundaries.
- HMAC verification for device-origin uploads.
- Nonce replay protection and rate limiting.
- Structured audit logs for state-changing operations.

### 4.2 Data Governance and Privacy

Current governance capabilities include:
- Biometric template lifecycle management.
- Retention enforcement jobs.
- Right-to-deletion pathways for biometric records.
- Governance overview endpoints for fairness/retention/drift visibility.

### 4.3 Fairness and Drift

Operational governance tracks:
- Group-level performance metrics (for example precision/recall/FMR/FNMR by cohort).
- Disparity ratios and scheduled fairness audits.
- Camera-domain drift signals for proactive remediation.

### 4.4 Ongoing Compliance Work

Areas commonly requiring recurring review:
- Demographic performance parity over time.
- Data deletion depth and historical analytics constraints.
- Anti-spoof effectiveness against evolving replay methods.
- Region-specific legal policy updates for biometric systems.

## 5. Canonical Links and Runbooks

Canonical root docs:
- README.md
- SYSTEM_OVERVIEW.md
- REQUIREMENTS.md
- ARCHITECTURE.md
- ROADMAP.md
- SECURITY_REVIEW_2026-04-08.md

Runbooks:
- docs/runbooks/workspace_sync.md
- docs/runbooks/daily_github_push.md
- docs/runbooks/quarterly_review.md
- docs/runbooks/remote_server_handoff.md

Phase execution status summary:
- docs/PHASE_STATUS.md
