"""Job control API (DESIGN.md §10.2, §10.5, §10.6, §14.1).

Snapshot, cancel, retry, and SSE progress — all owner-scoped (a job that is not
the caller's is a 404, §14.2). The SSE stream reconstructs state from the durable
snapshot (pub/sub is not required); polling ``GET /jobs/{id}`` stays the fallback.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ...db.models import ExtractionJob
from ...schemas.jobs import JobRead
from ...services.audit import record_audit
from ..deps import CurrentUser, DbSession

router = APIRouter(tags=["jobs"])

_TERMINAL = ("succeeded", "failed", "cancelled")

# SSE poll cadence: how often the stream re-reads the durable snapshot, and how
# often it emits a keepalive comment while nothing changes.
_SSE_POLL_SECONDS = 1.0
_SSE_KEEPALIVE_SECONDS = 15.0


async def _owned_job(session, user, job_id: str) -> ExtractionJob:
    job = await session.scalar(
        select(ExtractionJob).where(
            ExtractionJob.id == job_id, ExtractionJob.owner_id == user.id
        )
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(job_id: str, user: CurrentUser, session: DbSession) -> JobRead:
    return JobRead.model_validate(await _owned_job(session, user, job_id))


def _sse(job: ExtractionJob) -> str:
    """One SSE frame: event id = durable progress sequence, data = job snapshot."""
    payload = JobRead.model_validate(job).model_dump_json()
    return f"id: {job.progress_sequence}\ndata: {payload}\n\n"


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str,
    user: CurrentUser,
    session: DbSession,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Server-Sent Events progress stream (DESIGN.md §10.2).

    Emits the authoritative snapshot whenever the durable ``progress_sequence``
    advances, using it as the SSE event id. A reconnect that sends ``Last-Event-ID``
    resumes from there — the client already has everything up to that sequence, and
    since progress is monotonic the current snapshot supersedes any it missed. The
    stream closes once the job reaches a terminal state.
    """
    await _owned_job(session, user, job_id)  # owner check up front (404 if not owned)

    try:
        last_seen = int(last_event_id) if last_event_id is not None else 0
    except ValueError:
        last_seen = 0

    # The stream outlives this handler; open short-lived sessions on the same
    # engine for each poll. Release the request-scoped session first so its pooled
    # connection is free for the stream (Starlette cancels the generator on client
    # disconnect, which ends the loop for a still-running job).
    sessionmaker = async_sessionmaker(session.bind, expire_on_commit=False)
    await session.close()

    async def stream() -> AsyncIterator[str]:
        nonlocal last_seen
        yield ": connected\n\n"
        idle = 0.0
        while True:
            async with sessionmaker() as s:
                job = await s.get(ExtractionJob, job_id)
            if job is None:
                return
            if job.progress_sequence > last_seen:
                last_seen = job.progress_sequence
                idle = 0.0
                yield _sse(job)
            if job.status in _TERMINAL:
                return
            await asyncio.sleep(_SSE_POLL_SECONDS)
            idle += _SSE_POLL_SECONDS
            if idle >= _SSE_KEEPALIVE_SECONDS:
                idle = 0.0
                yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
async def cancel_job(job_id: str, user: CurrentUser, session: DbSession) -> JobRead:
    job = await _owned_job(session, user, job_id)
    if job.status not in _TERMINAL:
        job.cancel_requested = True  # persisted request; a running worker acts on it
        if job.status == "queued":
            # Not yet claimed — cancel immediately.
            job.status = "cancelled"
            job.finished_at = datetime.now(UTC)
        record_audit(
            session, actor_user_id=user.id, action="job.cancel",
            resource_class="extraction_job", resource_id=job.id,
        )
        await session.commit()
        await session.refresh(job)
    return JobRead.model_validate(job)


@router.post("/jobs/{job_id}/retry", response_model=JobRead)
async def retry_job(job_id: str, user: CurrentUser, session: DbSession) -> JobRead:
    job = await _owned_job(session, user, job_id)
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only failed or cancelled jobs can be retried"
        )
    job.status = "queued"
    job.cancel_requested = False
    job.worker_id = None
    job.fencing_token = None
    job.lease_expires_at = None
    job.failure_code = None
    job.finished_at = None
    job.stage = None
    record_audit(
        session, actor_user_id=user.id, action="job.retry",
        resource_class="extraction_job", resource_id=job.id,
    )
    await session.commit()
    await session.refresh(job)
    return JobRead.model_validate(job)
