"""Job execution engine: claim, lease/fencing, cancellation (DESIGN.md §10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import ExtractionJob, SourceDocument, User, UserDocument
from app.worker.engine import JobContext, JobPreempted, process_one
from app.worker.processors import stub_processor
from app.worker.queue import claim_next_job


async def _seed(client, *, cancel: bool = False) -> tuple[str, str]:
    async with client.sessionmaker() as s:
        user = User(subject=uuid.uuid4().hex, email="w@example.com")
        s.add(user)
        await s.flush()
        source = SourceDocument(owner_id=user.id, source_type="upload", state="uploaded")
        s.add(source)
        await s.flush()
        doc = UserDocument(owner_id=user.id, source_id=source.id, title="t", state="processing")
        s.add(doc)
        await s.flush()
        job = ExtractionJob(
            owner_id=user.id, document_id=doc.id, status="queued", cancel_requested=cancel
        )
        s.add(job)
        await s.commit()
        return job.id, doc.id


async def _get_job(client, job_id: str) -> ExtractionJob:
    async with client.sessionmaker() as s:
        return await s.get(ExtractionJob, job_id)


async def _get_doc(client, doc_id: str) -> UserDocument:
    async with client.sessionmaker() as s:
        return await s.get(UserDocument, doc_id)


async def _expire_lease(client, job_id: str) -> None:
    async with client.sessionmaker() as s:
        job = await s.get(ExtractionJob, job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()


async def test_claim_marks_running_with_fencing(client):
    job_id, _ = await _seed(client)
    async with client.sessionmaker() as s:
        claimed = await claim_next_job(s, worker_id="w1")
    assert claimed is not None and claimed.id == job_id
    assert claimed.status == "running"
    assert claimed.attempt == 1
    assert claimed.fencing_token and claimed.worker_id == "w1"

    async with client.sessionmaker() as s:
        assert await claim_next_job(s, worker_id="w1") is None


async def test_process_one_succeeds_and_marks_document_ready(client):
    job_id, doc_id = await _seed(client)
    did = await process_one(client.sessionmaker, client.object_store, stub_processor)
    assert did is True

    job = await _get_job(client, job_id)
    assert job.status == "succeeded"
    assert job.overall_fraction == 1.0
    assert (await _get_doc(client, doc_id)).state == "ready"

    # Queue is now empty.
    assert await process_one(client.sessionmaker, client.object_store, stub_processor) is False


async def test_cancellation_stops_processing(client):
    job_id, doc_id = await _seed(client, cancel=True)
    assert await process_one(client.sessionmaker, client.object_store, stub_processor) is True
    assert (await _get_job(client, job_id)).status == "cancelled"
    assert (await _get_doc(client, doc_id)).state == "processing"  # never marked ready


async def test_expired_lease_is_reclaimed(client):
    job_id, _ = await _seed(client)
    async with client.sessionmaker() as s:
        first = await claim_next_job(s, worker_id="w1")
    await _expire_lease(client, job_id)
    async with client.sessionmaker() as s:
        second = await claim_next_job(s, worker_id="w2")

    assert second is not None and second.id == job_id
    assert second.attempt == 2
    assert second.worker_id == "w2"
    assert second.fencing_token != first.fencing_token


async def test_stale_worker_cannot_heartbeat(client):
    job_id, _ = await _seed(client)
    async with client.sessionmaker() as s:
        first = await claim_next_job(s, worker_id="w1")
    stale = JobContext(
        client.sessionmaker,
        client.object_store,
        job_id=job_id,
        fencing_token=first.fencing_token,
        lease_seconds=30,
    )
    # Another worker reclaims after the lease expires, taking a new fencing token.
    await _expire_lease(client, job_id)
    async with client.sessionmaker() as s:
        await claim_next_job(s, worker_id="w2")

    with pytest.raises(JobPreempted):
        await stale.heartbeat(stage="processing")
