"""Extraction worker runner (DESIGN.md §10).

Long-lived loop: claim a job (SKIP LOCKED), renew a lease with heartbeats, run
the extraction core under resource limits with cancellation checks, checkpoint
per-page text results, and publish an immutable artifact atomically.

This is the Phase 2 skeleton; the body is filled in there.

Run with:  python -m app.worker.run
"""

from __future__ import annotations

import asyncio

from ..config import get_settings


async def main() -> None:
    settings = get_settings()
    _ = settings.max_concurrent_extractions  # global concurrency cap (§17.6)
    raise NotImplementedError(
        "Worker loop — implemented in Phase 2 (DESIGN.md §10.3–§10.7)."
    )


if __name__ == "__main__":
    asyncio.run(main())
