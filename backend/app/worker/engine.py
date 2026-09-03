"""Job execution engine (DESIGN.md §10).

``process_one`` claims a job and runs a *processor* against a ``JobContext``. The
context writes durable progress guarded by the fencing token, renews the lease on
every heartbeat, and surfaces cancellation cooperatively. Terminal status is set
only if this worker still holds the fencing token, so a stale worker cannot
overwrite a newer attempt.

Processors are pluggable per job kind; this slice ships a stub. Real extraction
and the restricted-egress fetch processor land in later slices behind the same
``JobContext`` interface.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import ExtractionJob
from ..storage.base import ObjectStore
from .queue import claim_next_job


class JobCancelled(Exception):
    """Raised inside a heartbeat when cancellation has been requested."""


class JobPreempted(Exception):
    """Raised when this worker no longer owns the job (lease lost / superseded)."""


class JobContext:
    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        store: ObjectStore,
        *,
        job_id: str,
        fencing_token: str,
        lease_seconds: int,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.store = store
        self.job_id = job_id
        self.fencing_token = fencing_token
        self.lease_seconds = lease_seconds

    async def heartbeat(
        self,
        *,
        stage: str | None = None,
        stage_label: str | None = None,
        completed_units: int | None = None,
        total_units: int | None = None,
        unit: str | None = None,
        indeterminate: bool | None = None,
    ) -> None:
        """Persist progress + renew the lease. Raises if cancelled or preempted."""
        async with self.sessionmaker() as session:
            job = await session.get(ExtractionJob, self.job_id)
            if job is None or job.fencing_token != self.fencing_token or job.status != "running":
                raise JobPreempted()
            if job.cancel_requested:
                raise JobCancelled()

            now = datetime.now(UTC)
            changed = False
            if stage is not None and stage != job.stage:
                job.stage = stage
                changed = True
            if stage_label is not None:
                job.stage_label = stage_label
            if completed_units is not None and completed_units != job.completed_units:
                job.completed_units = completed_units
                changed = True
            if total_units is not None:
                job.total_units = total_units
            if unit is not None:
                job.unit = unit
            if indeterminate is not None:
                job.indeterminate = indeterminate
            if job.total_units:
                job.overall_fraction = min(1.0, job.completed_units / job.total_units)
            # A meaningful transition (new stage or work-unit progress) advances the
            # durable progress sequence so SSE clients get an event (§10.2).
            if changed:
                job.progress_sequence += 1
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            await session.commit()


Processor = Callable[[JobContext, ExtractionJob], Awaitable[None]]


async def _finish(
    ctx: JobContext, status: str, *, failure_code: str | None = None
) -> None:
    async with ctx.sessionmaker() as session:
        job = await session.get(ExtractionJob, ctx.job_id)
        # Only the current fence-holder may write terminal state; if a newer
        # attempt already succeeded, leave that success authoritative (§10.5).
        if job is None or job.fencing_token != ctx.fencing_token:
            return
        if job.status in ("succeeded", "failed", "cancelled"):
            return
        job.status = status
        job.failure_code = failure_code
        job.finished_at = datetime.now(UTC)
        job.progress_sequence += 1  # terminal transition emits a final SSE event
        await session.commit()


async def process_one(
    sessionmaker: async_sessionmaker,
    store: ObjectStore,
    processor: Processor,
    *,
    worker_id: str = "worker-1",
    lease_seconds: int = 30,
) -> bool:
    """Claim and run one job. Returns True if a job was processed, else False."""
    async with sessionmaker() as session:
        job = await claim_next_job(session, worker_id=worker_id, lease_seconds=lease_seconds)
    if job is None:
        return False

    ctx = JobContext(
        sessionmaker,
        store,
        job_id=job.id,
        fencing_token=job.fencing_token,
        lease_seconds=lease_seconds,
    )
    try:
        await processor(ctx, job)
    except JobCancelled:
        await _finish(ctx, "cancelled")
        return True
    except JobPreempted:
        return True  # another worker owns it now
    except Exception:
        await _finish(ctx, "failed", failure_code="INTERNAL_ERROR")
        return True

    await _finish(ctx, "succeeded")
    return True
