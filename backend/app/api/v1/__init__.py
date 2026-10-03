"""Versioned API router (DESIGN.md §14).

Feature routers are added per phase. Every mutation is authenticated,
per-user authorized (owner predicate), and idempotent where relevant.

Wired: uploads (direct upload), imports (portable-save analyze/commit),
documents (upload + fetch-by-identifier) + bookmarks (Phase 1),
jobs (snapshot / cancel / retry, Phase 2).
TODO by phase:
  - jobs: SSE event stream + resume (§10.2, §10.7, Phase 2)
  - artifacts: manifest, entries, figures, callouts (§14.1, Phase 3)
  - citations: profiles, preview (§14.1, Phase 4)
"""

from __future__ import annotations

from fastapi import APIRouter

from . import artifacts, documents, imports, jobs, uploads, workspaces

api_router = APIRouter()
api_router.include_router(uploads.router)
api_router.include_router(imports.router)
api_router.include_router(documents.router)
api_router.include_router(workspaces.router)
api_router.include_router(jobs.router)
api_router.include_router(artifacts.router)


@api_router.get("/ping", tags=["meta"])
async def ping() -> dict[str, str]:
    return {"pong": "v1"}
