"""Job control API: snapshot, cancel, retry (DESIGN.md §10, §14.1)."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import ExtractionJob


async def _fetch_doc(client, auth, token) -> str:
    r = await client.post(
        "/api/v1/documents",
        headers=auth(token),
        json={"source_type": "fetch", "patent_identifier": "US 12,262,260 B2"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _job_for(client, doc_id: str) -> str:
    async with client.sessionmaker() as s:
        return await s.scalar(select(ExtractionJob.id).where(ExtractionJob.document_id == doc_id))


async def test_get_job_snapshot(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc_id = await _fetch_doc(client, auth, tok)
    job_id = await _job_for(client, doc_id)

    r = await client.get(f"/api/v1/jobs/{job_id}", headers=auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["stage"] == "fetching_source"
    assert body["document_id"] == doc_id


async def test_get_job_is_owner_scoped(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc_id = await _fetch_doc(client, auth, tok)
    job_id = await _job_for(client, doc_id)

    other = make_token("intruder", "x@example.com")
    r = await client.get(f"/api/v1/jobs/{job_id}", headers=auth(other))
    assert r.status_code == 404


async def test_cancel_queued_job(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc_id = await _fetch_doc(client, auth, tok)
    job_id = await _job_for(client, doc_id)

    r = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "cancelled"
    assert body["cancel_requested"] is True


async def test_retry_after_cancel_requeues(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc_id = await _fetch_doc(client, auth, tok)
    job_id = await _job_for(client, doc_id)

    await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth(tok))
    r = await client.post(f"/api/v1/jobs/{job_id}/retry", headers=auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["cancel_requested"] is False


async def test_retry_running_job_conflicts(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc_id = await _fetch_doc(client, auth, tok)
    job_id = await _job_for(client, doc_id)
    async with client.sessionmaker() as s:
        job = await s.get(ExtractionJob, job_id)
        job.status = "running"
        await s.commit()

    r = await client.post(f"/api/v1/jobs/{job_id}/retry", headers=auth(tok))
    assert r.status_code == 409
