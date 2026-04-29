# Remote Server Setup Handoff Prompt (A10 GPU)

Copy/paste everything below into the agent running in the VS Code window connected to your remote server.

---

## Fill This First (Operator Context For Remote Agent)

Before you paste the main prompt into the remote agent, fill and prepend this context block so it knows exactly where to fetch files from.

```text
REMOTE_HANDOFF_CONTEXT
SOURCE_MODE=<git|archive_url|manual_scp>
SOURCE_REPO_URL=<ssh-or-https-repo-url>
SOURCE_BRANCH=<branch-name>
SOURCE_COMMIT=<full-commit-sha-or-EMPTY>
TARGET_PATH=<absolute-path-on-remote>
ARCHIVE_URL=<https-url-to-tar-or-zip-or-EMPTY>
ARCHIVE_SHA256=<sha256-or-EMPTY>
SCP_DROP_PATH=<absolute-remote-path-where-you-uploaded-tar-or-zip-or-EMPTY>

# Optional credentials notes for private repos/artifacts:
AUTH_NOTES=<token/ssh key already configured on remote? yes/no + short note>
```

Notes:
- Use `SOURCE_MODE=git` if the remote can reach your git remote directly.
- Use `SOURCE_MODE=archive_url` if you provide a downloadable artifact URL.
- Use `SOURCE_MODE=manual_scp` if you upload a tar/zip to the remote first, then the agent unpacks from `SCP_DROP_PATH`.
- If `SOURCE_COMMIT` is set, the agent must checkout exactly that commit after fetch.

---

```text
You are the setup orchestrator for the Attendance System V2 on a remote miniserver with NVIDIA A10 GPU.
Your goal is to ensure code is synced, environment is configured, services start correctly, and baseline validation passes.

Context:
- The audit fix prompts were already executed in the source workspace.
- This run is for remote server setup and validation.
- Prefer non-interactive commands only.
- You will receive a REMOTE_HANDOFF_CONTEXT block from the operator. Treat it as source-of-truth.

Skills requirement (must run before Phase 0):
- Access skills from the workspace `.github/skills` folder (preferred).
- Fallback to `.agents/skills` if `.github/skills` is missing.
- Load and read these files first (from preferred path, then fallback path):
  - `devops-infra-engineer/SKILL.md`
  - `qa-auditor/SKILL.md`
  - `web-backend-engineer/SKILL.md`
- If a listed skill file is missing in both locations, continue with best effort and report the missing path.

Mandatory source pickup behavior:
- Do NOT guess source paths, repo URLs, branch, or commit.
- Parse REMOTE_HANDOFF_CONTEXT first.
- If required fields are missing for the selected SOURCE_MODE, stop and ask only for missing values.
- Print a short "Resolved Source Plan" before executing transfer commands.

Hard rules:
1) Never use destructive git commands (no git reset --hard, no checkout --).
2) If a step fails, diagnose and continue with best safe fallback.
3) Use parallel subagents when two or three tasks are independent.
4) Keep changes minimal and operationally focused.
5) Report exact commands run and results.

=====================================================
PHASE 0 — PRECHECK + SOURCE SYNC
=====================================================

Run these steps first (sequential):
1) Detect OS, shell, GPU, Docker, Python, Git, and working directory.
2) Resolve source according to SOURCE_MODE:
  - If SOURCE_MODE=git:
    a) If TARGET_PATH exists and is a git repo:
      - show git remote -v
      - git fetch --all --prune
      - git checkout SOURCE_BRANCH (if provided)
      - git pull --ff-only
    b) If TARGET_PATH does not exist:
      - git clone --branch SOURCE_BRANCH SOURCE_REPO_URL TARGET_PATH
    c) If SOURCE_COMMIT is not EMPTY:
      - git checkout SOURCE_COMMIT
  - If SOURCE_MODE=archive_url:
    a) Create TARGET_PATH
    b) Download ARCHIVE_URL to /tmp (or platform equivalent)
    c) Verify SHA256 when ARCHIVE_SHA256 is provided
    d) Extract into TARGET_PATH
  - If SOURCE_MODE=manual_scp:
    a) Verify SCP_DROP_PATH exists
    b) Create TARGET_PATH
    c) Extract tar/zip from SCP_DROP_PATH into TARGET_PATH
3) cd TARGET_PATH and confirm expected files are present.
4) Confirm audit-fix files exist in repo:
   - .gitignore contains celerybeat-schedule* and triton_models/*/1/model.plan
   - REMOTE_SERVER_HANDOFF_PROMPT.md exists
  - AUDIT_FIX_PROMPTS.md exists
  - .github/skills/devops-infra-engineer/SKILL.md exists (or .agents fallback)
  - .github/skills/qa-auditor/SKILL.md exists (or .agents fallback)
  - .github/skills/web-backend-engineer/SKILL.md exists (or .agents fallback)
5) Print source pinning summary:
  - resolved mode
  - source repo/archive path
  - branch
  - commit currently checked out (git rev-parse HEAD when git mode)

Required checks:
- nvidia-smi
- docker --version
- docker compose version
- python --version
- git --version

=====================================================
PHASE 1 — PARALLEL WAVE (3 SUBAGENTS)
=====================================================

Launch 3 subagents in parallel after source sync:

Subagent A: "Env Preparation"
- Create .env from .env.example if missing.
- Set these required overrides for GPU server baseline:
  - APP_ENV=production
  - INSIGHTFACE_PROVIDER=CUDAExecutionProvider
  - ENABLE_TRITON=false
  - ENABLE_TRACKING=true
  - ENABLE_CROSS_CAMERA_REID=true
  - ENABLE_YOLOV12=false (keep off until verified)
  - ENABLE_LVFACE=false (keep off until verified)
  - ENABLE_CODEFORMER=false (enable later after baseline)
  - ENABLE_DISKANN=false
  - ENABLE_RPPG_LIVENESS=false
  - ENABLE_FLASH_LIVENESS=false
- Ensure JWT_SECRET_KEY is not placeholder in production.
- Report final effective env diff (only changed keys).

Subagent B: "Model and Artifact Audit"
- Verify required model assets under models/.
- List missing model files expected by runtime.
- Verify triton_models placeholders are not used in baseline mode.
- Do not enable Triton.
- Produce a missing-assets checklist.

Subagent C: "Infra Bring-up"
- Start only foundational services first:
  - docker compose up -d postgres redis
- Wait for health checks.
- Run DB migration:
  - alembic upgrade head
- Optionally seed test data if empty:
  - python -m scripts.seed
- Report DB/Redis readiness.

Wait for all 3 to complete, then continue.

=====================================================
PHASE 2 — APP + WORKER STARTUP (SEQUENTIAL)
=====================================================

1) Start app and CV worker:
- docker compose up -d app cv-worker

2) Verify services:
- docker compose ps
- check logs for app and cv-worker (tail enough lines to confirm healthy startup)
- curl health endpoint:
  - http://localhost:8000/health
- curl ready endpoint:
  - http://localhost:8000/ready

3) If failures occur:
- capture error snippet
- apply minimal fix
- restart only affected service

=====================================================
PHASE 3 — VALIDATION WAVE (PARALLEL WHERE POSSIBLE)
=====================================================

Run validation with 2 parallel subagents:

Subagent D: "Code/Test Validation"
- Run:
  - python -m py_compile backend/main.py
  - python -m py_compile backend/workers/cv_tasks.py
  - python -m py_compile backend/services/ai_pipeline.py
  - python -m pytest tests/test_units.py -q --tb=short
- Report pass/fail and first failing trace if any.

Subagent E: "Runtime Validation"
- Run:
  - python -m scripts.smoke_test
- Report each component status (FastAPI, Celery, Redis, PostgreSQL, AI pipeline).

After both complete:
- Summarize total pass/fail counts.
- If smoke fails due to optional features, disable only the failing optional flag and re-run smoke test once.

=====================================================
PHASE 4 — OPTIONAL GPU PROMOTION CHECKS (DO NOT BLOCK BASELINE)
=====================================================

If baseline is green, perform optional staged checks (sequential):
1) Detector benchmark:
   - python -m scripts.baseline_eval
2) Model comparison:
   - python -m scripts.model_comparison
3) ANN benchmark:
   - python -m scripts.ann_benchmark

Do not enable Triton in this phase.

=====================================================
PHASE 5 — FINAL REPORT
=====================================================

Return a concise setup report with:
1) Host info (GPU driver/CUDA visibility, Docker versions)
2) Git sync status (branch + latest commit)
3) Env keys changed
4) Services up/down
5) Migration status
6) Test status
7) Smoke test status
8) Missing model files (if any)
9) Exact next command to continue toward Triton enablement

If blocked, clearly state blocker + minimum required user input.
```

---

## Optional Follow-up Prompt (Triton Enablement Later)

Use this only after baseline setup is fully green.

```text
Now run a Triton enablement readiness pass:
1) Verify required ONNX source files exist under models/ for yolov12, arcface, adaface, lvface, realesrgan, antispoof.
2) If missing, list exact filenames and stop.
3) If all present, run scripts/build_triton_engines.ps1 (or Linux equivalent) to generate model.plan files.
4) Set ENABLE_TRITON=true and TRITON_URL=triton:8001.
5) Start triton service and rerun smoke test.
6) Compare latency/throughput vs ENABLE_TRITON=false baseline.
Return decision: keep Triton enabled or defer.
```
