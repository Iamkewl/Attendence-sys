# Security Review: Attendance System

Date: 2026-04-08
Reviewer: security-specialist
Verdict: CONDITIONAL (critical issues identified; fixes in progress)

## Findings

| # | Severity | OWASP | Description | Evidence | Recommended Fix |
|---|----------|-------|-------------|----------|-----------------|
| 1 | Critical | A01 Broken Access Control | Public register path allowed self-assigned privileged roles. | backend/schemas/auth.py, backend/api/v1/auth.py, backend/services/auth_service.py | Force public registration to student role only; keep privileged role assignment in admin-only user management endpoints. |
| 2 | Critical | A07 Auth Failures | Ingest accepted snapshots/clips without enforcing device secret and HMAC verification. | backend/api/v1/ingest.py | Verify device exists, validate secret, verify signature, enforce schedule-room binding, then process payload. |
| 3 | High | A01 Broken Access Control | Device WebSocket accepted connections without validating provided secret. | backend/api/v1/websocket.py | Validate device ID + room ID + secret before accepting and registering connection. |
| 4 | High | A01 Broken Access Control | Attendance SSE and attendance WebSocket streams were unauthenticated. | backend/api/v1/sse.py, backend/api/v1/websocket.py | Require access token and enforce RBAC for live attendance streams. |
| 5 | High | A05 Security Misconfiguration | JWT secret placeholder could be used if environment is not set correctly. | backend/core/config.py | Add non-development startup guard that rejects placeholder/weak secrets. |
| 6 | Medium | A03/A07 | Refresh token currently stored in localStorage (XSS token theft risk). | frontend/src/api/client.js | Move refresh token to HttpOnly cookie or enforce strict CSP and shortened token lifetime until migration. |

## Threat Model Summary

### Assets
- Attendance records and analytics
- User identities and role privileges
- Device identity and ingest trust boundary
- Long-lived refresh tokens

### Primary Abuse Cases
- Student elevates account to admin via registration payload manipulation.
- Attacker forges ingest uploads to mark attendance fraudulently.
- Unauthorized observer subscribes to live attendance streams.

## Planned Mitigations

1. Block role escalation in public register flow.
2. Enforce device secret and HMAC verification on ingest endpoints.
3. Enforce device secret verification on device WebSocket connections.
4. Enforce authenticated access for SSE/attendance WebSocket endpoints.
5. Add configuration guard for non-dev JWT secret requirements.

## Pending Decisions / Permissions (TODO)

1. TODO: Refresh token storage migration.
	Current state: refresh token is stored in localStorage.
	Decision needed: approve migration to HttpOnly secure cookie flow (backend + frontend auth contract changes).

2. TODO: CSP and security headers policy.
	Current state: frontend/backend do not enforce a strict CSP header set.
	Decision needed: approve CSP policy and rollout strategy to avoid breaking existing scripts/styles during transition.

3. TODO: WebSocket auth transport model for browser clients.
	Current state: attendance WebSocket auth uses token query parameter.
	Decision needed: approve moving to ticket-based or cookie-based WS auth to avoid token-in-query exposure in logs.

4. TODO: Device secret lifecycle policy.
	Current state: device secrets are validated, but rotation cadence and operational reset flow are not formalized.
	Decision needed: approve key rotation interval and emergency revocation procedure.

5. TODO: Production secret governance.
	Current state: non-dev guard exists for weak JWT secrets.
	Decision needed: define final production source of truth (Vault/Secret Manager) and rotation ownership.

## Validation Plan

- Unit/integration checks for denied unauthorized registration role assignments.
- Negative tests for ingest with bad/missing secret and invalid signature.
- WebSocket/SSE unauthorized access tests (missing/invalid token).
- Startup check validation for weak JWT secret in non-dev mode.

## Status

- Documented for review and retained in repository.
- Critical/high fixes are being implemented in current build continuation.

## Phase 7 Liveness Hardening Update (2026-04-10)

### Newly Implemented Controls

1. Tier 4 rPPG liveness path added in `backend/services/liveness.py`:
	- ROI extraction from forehead + cheeks
	- physiological band analysis (0.7-4.0 Hz)
	- signal quality ratio threshold gate
	- feature-flag controlled (`enable_rppg_liveness`)

2. Tier 5 flash scattering path added in `backend/services/liveness.py`:
	- pre-flash/post-flash differential analysis
	- chromatic + edge + texture scoring
	- camera capability gated via camera profiles (`supports_flash_liveness`)
	- feature-flag controlled (`enable_flash_liveness`)

3. WebSocket protocol support for flash-pair acquisition:
	- new `FLASH_CAPTURE` action dispatched by orchestrator
	- no impact to existing `CAPTURE` flow

### Closed Security Items

1. Liveness gap against high-quality replay attacks: partially closed via Tier 4 rPPG.
2. Liveness gap against mask/material presentation attacks: partially closed via Tier 5 flash scattering.
3. Attendance flow resilience requirement: maintained via strict flag/capability gating (no hard dependency when unsupported).

### Accepted Risks (Current)

1. rPPG is quality-sensitive and needs enough temporal samples (recommended `burst_capture_count >= rppg_min_frames` when Tier 4 is enabled).
2. Flash liveness depends on device hardware and controlled lighting; unsupported cameras must skip Tier 5.
3. Depth consistency anti-replay remains an extension interface only until stereo/structured-light hardware is available.
