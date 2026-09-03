"""SSE progress stream: GET /jobs/{id}/events (DESIGN.md §10.2)."""

from __future__ import annotations

import asyncio
import uuid

from app.api.v1 import jobs as jobs_api
from app.db.models import ExtractionJob, User


async def _seed_job(
    client, *, status="running", sequence=1, stage="extracting_ocr"
) -> tuple[str, str]:
    async with client.sessionmaker() as s:
        user = User(subject=uuid.uuid4().hex, email="o@example.com")
        s.add(user)
        await s.flush()
        job = ExtractionJob(
            owner_id=user.id, status=status, stage=stage,
            progress_sequence=sequence, indeterminate=False,
        )
        s.add(job)
        await s.commit()
        return job.id, user.id


def _frames(body: str) -> list[str]:
    """Split an SSE body into event blocks (comments dropped)."""
    blocks = [b for b in body.split("\n\n") if b.strip()]
    return [b for b in blocks if any(line.startswith("data:") for line in b.splitlines())]


async def test_terminal_job_streams_final_snapshot_then_closes(client, make_token, auth):
    tok = make_token("o", "o@example.com")
    job_id, owner_id = await _seed_job(client, status="succeeded", sequence=4)
    # The job must belong to the token's user; align the seeded owner via subject.
    async with client.sessionmaker() as s:
        user = await s.get(User, owner_id)
        user.subject = "o"
        await s.commit()

    async with client.stream("GET", f"/api/v1/jobs/{job_id}/events", headers=auth(tok)) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = (await resp.aread()).decode()

    frames = _frames(body)
    assert len(frames) == 1
    assert "id: 4" in frames[0]
    assert '"status":"succeeded"' in frames[0]


async def test_last_event_id_suppresses_already_seen(client, make_token, auth):
    tok = make_token("o", "o@example.com")
    job_id, owner_id = await _seed_job(client, status="succeeded", sequence=4)
    async with client.sessionmaker() as s:
        user = await s.get(User, owner_id)
        user.subject = "o"
        await s.commit()

    headers = {**auth(tok), "Last-Event-ID": "4"}
    async with client.stream("GET", f"/api/v1/jobs/{job_id}/events", headers=headers) as resp:
        body = (await resp.aread()).decode()

    # Client already saw sequence 4 (a terminal event) — nothing new to send.
    assert _frames(body) == []


async def test_events_owner_scoped(client, make_token, auth):
    job_id, owner_id = await _seed_job(client, status="succeeded", sequence=2)
    async with client.sessionmaker() as s:
        user = await s.get(User, owner_id)
        user.subject = "owner"
        await s.commit()

    intruder = make_token("intruder", "x@example.com")
    r = await client.get(f"/api/v1/jobs/{job_id}/events", headers=auth(intruder))
    assert r.status_code == 404


async def test_stream_emits_update_then_closes_on_terminal(client, make_token, auth, monkeypatch):
    monkeypatch.setattr(jobs_api, "_SSE_POLL_SECONDS", 0.02)
    tok = make_token("o", "o@example.com")
    job_id, owner_id = await _seed_job(client, status="running", sequence=1)
    async with client.sessionmaker() as s:
        user = await s.get(User, owner_id)
        user.subject = "o"
        await s.commit()

    async def advance() -> None:
        await asyncio.sleep(0.05)
        async with client.sessionmaker() as s:
            job = await s.get(ExtractionJob, job_id)
            job.stage = "persisting_artifact"
            job.status = "succeeded"
            job.progress_sequence = 2
            await s.commit()

    task = asyncio.create_task(advance())
    async with client.stream("GET", f"/api/v1/jobs/{job_id}/events", headers=auth(tok)) as resp:
        body = (await resp.aread()).decode()
    await task

    frames = _frames(body)
    # First the running snapshot (seq 1), then the terminal snapshot (seq 2).
    assert "id: 1" in frames[0]
    assert '"status":"running"' in frames[0]
    assert "id: 2" in frames[-1]
    assert '"status":"succeeded"' in frames[-1]
