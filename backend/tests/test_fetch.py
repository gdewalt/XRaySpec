"""Fetch-by-identifier document creation (DESIGN.md §11.2, §14.1)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.models import ExtractionArtifact, ExtractionJob, SourceDocument, User, UserDocument
from app.extraction.config import DEFAULT_CONFIG


async def test_fetch_grant_creates_processing_document(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    r = await client.post(
        "/api/v1/documents",
        headers=auth(tok),
        json={"source_type": "fetch", "patent_identifier": "US 12,262,260 B2"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "US 12,262,260 B2"  # defaults to the display name
    assert body["patent_number"] == "US 12,262,260 B2"
    assert body["state"] == "processing"

    # It shows up in the owner's list.
    listed = await client.get("/api/v1/documents", headers=auth(tok))
    listed_document = next(d for d in listed.json()["items"] if d["id"] == body["id"])
    assert listed_document["patent_number"] == "US 12,262,260 B2"


async def test_fetch_application_identifier(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    r = await client.post(
        "/api/v1/documents",
        headers=auth(tok),
        json={"source_type": "fetch", "patent_identifier": "US 2024/0123456 A1"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "US 2024/0123456 A1"


async def test_fetch_custom_title_overrides_display(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    r = await client.post(
        "/api/v1/documents",
        headers=auth(tok),
        json={"source_type": "fetch", "patent_identifier": "US12262260B2", "title": "My case"},
    )
    assert r.status_code == 201
    assert r.json()["title"] == "My case"


async def test_fetch_requires_identifier(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    r = await client.post("/api/v1/documents", headers=auth(tok), json={"source_type": "fetch"})
    assert r.status_code == 400


async def test_fetch_rejects_bad_identifier(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    r = await client.post(
        "/api/v1/documents",
        headers=auth(tok),
        json={"source_type": "fetch", "patent_identifier": "not a patent"},
    )
    assert r.status_code == 400
    assert "unrecognized" in r.text


async def test_fetch_requires_auth(client):
    r = await client.post(
        "/api/v1/documents",
        json={"source_type": "fetch", "patent_identifier": "US12262260B2"},
    )
    assert r.status_code == 401


async def test_repeat_patent_import_reuses_matching_completed_artifact(client, make_token, auth):
    token = make_token("owner", "o@example.com")
    assert (await client.get("/api/v1/documents", headers=auth(token))).status_code == 200
    payload = {
        "schema_version": 2,
        "source_sha256": "same-pdf",
        "config_hash": DEFAULT_CONFIG.config_hash(),
        "disposition": "complete",
    }
    async with client.sessionmaker() as session:
        owner_id = await session.scalar(select(User.id).where(User.subject == "owner"))
        source = SourceDocument(
            owner_id=owner_id,
            source_type="fetch",
            patent_canonical="US12262260B2",
            sha256="same-pdf",
            pdf_object_key=f"sources/{owner_id}/existing.pdf",
            state="uploaded",
        )
        session.add(source)
        await session.flush()
        artifact = ExtractionArtifact(
            source_id=source.id,
            schema_version=2,
            disposition="complete",
            manifest_object_key=f"artifacts/{owner_id}/existing.json.gz",
            is_active=True,
            ready_at=datetime.now(UTC),
        )
        session.add(artifact)
        await session.flush()
        session.add(
            UserDocument(
                owner_id=owner_id,
                source_id=source.id,
                active_artifact_id=artifact.id,
                title="Existing patent title",
                state="ready",
            )
        )
        await session.commit()
        source_id = source.id
        artifact_id = artifact.id
    await client.object_store.put(
        f"artifacts/{owner_id}/existing.json.gz",
        gzip.compress(json.dumps(payload).encode()),
    )

    response = await client.post(
        "/api/v1/documents",
        headers=auth(token),
        json={"source_type": "fetch", "patent_identifier": "US12262260B2"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "ready"
    assert body["title"] == "Existing patent title"
    async with client.sessionmaker() as session:
        replacement = await session.get(UserDocument, body["id"])
        jobs = await session.scalar(
            select(func.count())
            .select_from(ExtractionJob)
            .where(ExtractionJob.document_id == body["id"])
        )
    assert replacement.source_id == source_id
    assert replacement.active_artifact_id == artifact_id
    assert jobs == 0
