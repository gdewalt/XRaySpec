"""Job control API (DESIGN.md §10.2, §10.5, §10.6, §14.1).

Snapshot, cancel, and retry — all owner-scoped (a job that is not the caller's is
a 404, §14.2). SSE progress streaming and resume land in later slices; the
snapshot here is the poll fallback the UI already uses.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ...db.models import ExtractionJob
from ...schemas.jobs import JobRead
from ...services.audit import record_audit
from ..deps import CurrentUser, DbSession

router = APIRouter(tags=["jobs"])

_TERMINAL = ("succeeded", "failed", "cancelled")


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
