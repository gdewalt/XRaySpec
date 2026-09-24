"""FastAPI entrypoint (DESIGN.md §5, §18.1).

The web tier never parses PDFs or runs OCR; that happens only in the isolated
worker. This module wires health checks, security headers, rate limiting, and the
versioned API router.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .api.deps import DbSession, Storage
from .api.v1 import api_router
from .config import get_settings
from .middleware import RateLimitMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)

app = FastAPI(
    title="X-Ray Spec API",
    version="0.1.0",
    description="Hosted patent specification viewer — see DESIGN.md.",
)

_settings = get_settings()

# Starlette wraps the last-added middleware outermost. Add the rate limiter first,
# then SecurityHeaders, so headers are stamped on *every* response — including a
# 429 emitted by the limiter.
app.add_middleware(
    RateLimitMiddleware,
    per_minute=_settings.rate_limit_per_minute,
    trust_forwarded=_settings.trust_forwarded_for,
)
app.add_middleware(
    SecurityHeadersMiddleware,
    enable_hsts=_settings.environment == "production",
)


@app.get("/live", tags=["health"])
async def live() -> dict[str, str]:
    """Process liveness only (DESIGN.md §18.1)."""
    return {"status": "live"}


@app.get("/ready", tags=["health"])
async def ready(response: Response, session: DbSession, store: Storage) -> dict[str, str]:
    """Readiness: required backing services reachable for this role (DESIGN.md §18.1).

    Verifies the database and object store are reachable. Returns 503 with a
    content-free reason if either check fails, so the edge stops routing traffic
    to a replica that cannot serve.
    """
    checks: dict[str, str] = {}
    ok = True

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # pragma: no cover - exercised via dependency override in tests
        logger.exception("readiness: database check failed")
        checks["database"] = "unavailable"
        ok = False

    try:
        # stat on a sentinel key proves reachability without requiring the object.
        await store.stat("__readiness_probe__")
        checks["storage"] = "ok"
    except Exception:  # pragma: no cover
        logger.exception("readiness: storage check failed")
        checks["storage"] = "unavailable"
        ok = False

    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", **checks}
    return {"status": "ready", **checks}


app.include_router(api_router, prefix="/api/v1")


@app.get("/runtime-config.js", include_in_schema=False)
async def runtime_config() -> Response:
    """Public browser configuration. The anon key is intentionally public;
    service-role and JWT-signing secrets are never included."""
    import json

    payload = {
        "supabaseUrl": _settings.supabase_project_url,
        "supabaseAnonKey": _settings.supabase_anon_key,
        "environment": _settings.environment,
    }
    return Response(
        content=f"window.__XRAY_CONFIG__ = {json.dumps(payload)};",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


if _settings.frontend_dist_dir:
    frontend_dir = Path(_settings.frontend_dist_dir)
    if frontend_dir.is_dir():
        # Registered last so API, health, docs, and runtime config keep priority.
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    else:
        logger.warning("frontend distribution directory does not exist: %s", frontend_dir)
