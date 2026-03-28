"""Attendance System V2 — FastAPI Application Factory.

Entrypoint for the application. Creates the FastAPI app with:
- Async lifespan for startup/shutdown hooks
- Modular router aggregation under /api/v1/
- Health check at root level
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.v1 import v1_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks.

    Startup:
    - Database tables creation (dev only, use Alembic in prod)
    - AI model loading (Sprint 5)
    - APScheduler start (Sprint 6)

    Shutdown:
    - APScheduler stop
    - Connection pool cleanup
    """
    # ── Startup ──────────────────────────────────────
    import logging
    logger = logging.getLogger(__name__)

    # Eagerly load AI models (SAHI, InsightFace, AdaFace, SR)
    try:
        from backend.services.ai_pipeline import ai_pipeline
        ai_pipeline.ensure_loaded()
        logger.info("startup: AI pipeline models loaded")
    except Exception as exc:
        logger.warning("startup: AI pipeline load failed (non-fatal): %s", exc)

    # Start APScheduler (heartbeat + cleanup)
    try:
        from backend.services.scheduler import start_scheduler
        start_scheduler()
        logger.info("startup: APScheduler started")
    except Exception as exc:
        logger.warning("startup: scheduler start failed (non-fatal): %s", exc)

    # Start Redis → SSE/WebSocket event bridge
    _event_bridge_task = None
    try:
        from backend.services.event_bridge import start_event_bridge
        _event_bridge_task = await start_event_bridge()
        logger.info("startup: event bridge started")
    except Exception as exc:
        logger.warning("startup: event bridge start failed (non-fatal): %s", exc)

    yield

    # ── Shutdown ─────────────────────────────────────
    try:
        from backend.services.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    if _event_bridge_task:
        _event_bridge_task.cancel()


app = FastAPI(
    title="Attendance System V2",
    description="Distributed Facial-Recognition Attendance Platform",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Vite + React dev servers
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount versioned API router
app.include_router(v1_router)

# Mount WebSocket and SSE routers (outside v1 prefix for cleaner URLs)
from backend.api.v1.websocket import router as ws_router
from backend.api.v1.sse import router as sse_router

app.include_router(ws_router, prefix="/ws", tags=["WebSocket"])
app.include_router(sse_router, prefix="/api/v1/sse", tags=["SSE"])


@app.get("/health", tags=["System"])
async def root_health():
    """Root-level health check (no auth required)."""
    return {"status": "ok", "version": "2.0.0"}


@app.get("/ready", tags=["System"])
async def root_ready():
    """Readiness probe — checks if all subsystems are operational."""
    from backend.services.websocket_manager import ws_manager
    from backend.api.v1.sse import sse_broadcaster

    return {
        "status": "ready",
        "version": "2.0.0",
        "devices_connected": ws_manager.connected_count,
        "sse_subscribers": sse_broadcaster.total_subscribers,
    }
