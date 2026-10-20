"""Extraction worker runner (DESIGN.md §10).

Long-lived loop: claim a job (SKIP LOCKED), run its processor under a
lease/fencing token with cooperative cancellation, and publish. No inbound
network; no platform secrets beyond storage/db credentials.

Run with:  python -m app.worker.run
"""

from __future__ import annotations

import asyncio
import logging
import os

from ..config import get_settings
from ..db.base import get_sessionmaker
from ..storage.base import ObjectStore
from .engine import process_one
from .processors import extraction_processor

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _build_store() -> ObjectStore:
    settings = get_settings()
    if settings.storage_backend == "memory":
        from ..storage.memory import MemoryObjectStore

        return MemoryObjectStore()
    if settings.storage_backend == "local":
        from ..storage.local import LocalFileObjectStore

        return LocalFileObjectStore(settings.local_storage_dir)
    from ..storage.supabase import SupabaseObjectStore

    return SupabaseObjectStore(
        settings.supabase_project_url, settings.supabase_service_key, settings.storage_bucket
    )


async def main() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    store = _build_store()
    logger.info(
        "extraction worker started storage_backend=%s bucket=%s",
        settings.storage_backend,
        settings.storage_bucket if settings.storage_backend == "supabase" else "n/a",
    )
    idle_backoff = 1.0
    idle_seconds = 0.0
    while True:
        did_work = await process_one(sessionmaker, store, extraction_processor)
        if did_work:
            idle_seconds = 0.0
        else:
            idle_seconds += idle_backoff
            if idle_seconds >= 60.0:
                logger.info("extraction worker is healthy and waiting for a queued job")
                idle_seconds = 0.0
        await asyncio.sleep(0 if did_work else idle_backoff)


if __name__ == "__main__":
    asyncio.run(main())
