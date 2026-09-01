"""Versioned API router (DESIGN.md §14).

Feature routers are added per phase. Every mutation is authenticated,
per-user authorized (owner predicate), and idempotent where relevant.

TODO (by phase):
  - documents.py   uploads, fetch-by-identifier, list/read/delete (§14.1, Phase 1)
  - jobs.py        status snapshot, SSE events, cancel/retry/resume (§14.1, Phase 2)
  - artifacts.py   manifest, entries, figures, callouts (§14.1, Phase 3)
  - citations.py   profiles, preview (§14.1, Phase 4)
"""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter()


@api_router.get("/ping", tags=["meta"])
async def ping() -> dict[str, str]:
    return {"pong": "v1"}
