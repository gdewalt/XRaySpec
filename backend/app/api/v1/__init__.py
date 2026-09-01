"""Versioned API router (DESIGN.md §14).

Feature routers are added per phase. Every mutation is authenticated,
per-user authorized (owner predicate), and idempotent where relevant.

Wired: documents + bookmarks (Phase 1).
TODO by phase:
  - ingestion: uploads grant/complete, fetch-by-identifier (§11, Phase 1 next slice)
  - jobs: status snapshot, SSE events, cancel/retry/resume (§14.1, Phase 2)
  - artifacts: manifest, entries, figures, callouts (§14.1, Phase 3)
  - citations: profiles, preview (§14.1, Phase 4)
"""

from __future__ import annotations

from fastapi import APIRouter

from . import documents

api_router = APIRouter()
api_router.include_router(documents.router)


@api_router.get("/ping", tags=["meta"])
async def ping() -> dict[str, str]:
    return {"pong": "v1"}
