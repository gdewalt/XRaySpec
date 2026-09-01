"""FastAPI entrypoint (DESIGN.md §5, §18.1).

The web tier never parses PDFs or runs OCR; that happens only in the isolated
worker. This module wires health checks, security headers, and the versioned API
router.
"""

from __future__ import annotations

from fastapi import FastAPI

from .api.v1 import api_router
from .config import get_settings

app = FastAPI(
    title="X-Ray Spec API",
    version="0.1.0",
    description="Hosted patent specification viewer — see DESIGN.md.",
)


@app.get("/live", tags=["health"])
async def live() -> dict[str, str]:
    """Process liveness only (DESIGN.md §18.1)."""
    return {"status": "live"}


@app.get("/ready", tags=["health"])
async def ready() -> dict[str, str]:
    """Readiness: required backing services reachable for this role.

    TODO: verify database and object-store access (DESIGN.md §18.1).
    """
    _ = get_settings()
    return {"status": "ready"}


app.include_router(api_router, prefix="/api/v1")
