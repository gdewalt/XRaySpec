"""Extraction worker runner (DESIGN.md §10).

Long-lived loop: claim a job (SKIP LOCKED), run its processor under a
lease/fencing token with cooperative cancellation, and publish. No inbound
network; no platform secrets beyond storage/db credentials.

Run with:  python -m app.worker.run
"""

from __future__ import annotations

import asyncio

from ..config import get_settings
from ..db.base import get_sessionmaker
from ..storage.base import ObjectStore
from .engine import process_one
from .processors import stub_processor


def _build_store() -> ObjectStore:
    settings = get_settings()
    if settings.storage_backend == "memory":
        from ..storage.memory import MemoryObjectStore

        return MemoryObjectStore()
    from ..storage.supabase import SupabaseObjectStore

    return SupabaseObjectStore(
        settings.supabase_project_url, settings.supabase_service_key, settings.storage_bucket
    )


async def main() -> None:
    sessionmaker = get_sessionmaker()
    store = _build_store()
    idle_backoff = 1.0
    while True:
        did_work = await process_one(sessionmaker, store, stub_processor)
        await asyncio.sleep(0 if did_work else idle_backoff)


if __name__ == "__main__":
    asyncio.run(main())
