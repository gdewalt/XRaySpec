"""Postgres-backed job queue: claim, lease, reap (DESIGN.md §5.1, §10.3, §10.4).

The job table *is* the queue — inserting a job row is the enqueue, so there is no
broker, outbox, or dispatcher. A worker claims the oldest claimable job and takes
a fencing token + short lease; expired-lease jobs become claimable again.

The claim uses ``SELECT … FOR UPDATE SKIP LOCKED`` on PostgreSQL so many workers
claim disjoint jobs without contention. SQLite (tests) has no ``SKIP LOCKED``;
there the plain select is safe because test access is serial, and the guarded
conditional UPDATE still prevents a double-claim.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ExtractionJob


def _is_postgres(session: AsyncSession) -> bool:
    try:
        return session.get_bind().dialect.name == "postgresql"
    except Exception:  # pragma: no cover - defensive
        return False


def _claimable(now: datetime):
    return or_(
        ExtractionJob.status == "queued",
        and_(ExtractionJob.status == "running", ExtractionJob.lease_expires_at < now),
    )


async def claim_next_job(
    session: AsyncSession, *, worker_id: str, lease_seconds: int = 30
) -> ExtractionJob | None:
    """Atomically claim the oldest claimable job, or return ``None`` if none.

    Sets the job ``running`` with a new fencing token, incremented attempt, and a
    fresh lease. Re-claims jobs whose lease has expired (a crashed worker).
    """
    now = datetime.now(UTC)

    pick = (
        select(ExtractionJob.id)
        .where(_claimable(now))
        .order_by(ExtractionJob.created_at)
        .limit(1)
    )
    if _is_postgres(session):
        pick = pick.with_for_update(skip_locked=True)

    job_id = await session.scalar(pick)
    if job_id is None:
        return None

    fencing = uuid.uuid4().hex
    result = await session.execute(
        update(ExtractionJob)
        .where(ExtractionJob.id == job_id, _claimable(now))
        .values(
            status="running",
            attempt=ExtractionJob.attempt + 1,
            worker_id=worker_id,
            fencing_token=fencing,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            failure_code=None,
            finished_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:  # lost the race to another worker
        await session.rollback()
        return None

    await session.commit()
    return await session.get(ExtractionJob, job_id)


async def reap_expired_jobs(session: AsyncSession) -> int:
    """Requeue jobs whose lease has expired (bounded recovery, §10.4).

    Claiming already reclaims expired jobs; this makes their state explicit for
    observability and returns the number reset.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        update(ExtractionJob)
        .where(ExtractionJob.status == "running", ExtractionJob.lease_expires_at < now)
        .values(status="queued", worker_id=None, fencing_token=None, lease_expires_at=None)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return result.rowcount
