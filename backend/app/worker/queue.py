"""Postgres-backed job queue (DESIGN.md §5.1, §10.3).

The job table *is* the queue — inserting a job row is the enqueue, so there is no
broker, no outbox, and no dispatcher. A worker claims the oldest ready job with
``SELECT ... FOR UPDATE SKIP LOCKED``, which lets many workers claim disjoint
jobs without contention.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Claim one ready job atomically. Lease/fencing columns (§10.4) are filled in
# with the worker implementation in Phase 2.
CLAIM_SQL = text(
    """
    SELECT id
    FROM extraction_jobs
    WHERE status = 'queued'
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
    """
)


async def claim_next_job(session: AsyncSession) -> str | None:
    """Claim the next queued job id, or ``None`` if the queue is empty.

    TODO: within the same transaction, mark the row ``running`` and record a
    fencing token + lease expiry (DESIGN.md §10.4).
    """
    result = await session.execute(CLAIM_SQL)
    row = result.first()
    return row[0] if row else None
