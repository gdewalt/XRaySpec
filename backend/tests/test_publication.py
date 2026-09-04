"""Atomic artifact publication (DESIGN.md §9.2)."""

from __future__ import annotations

import gzip
import json
import uuid

from app.db.models import ExtractionArtifact, SourceDocument, User, UserDocument
from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import extract_from_pages
from app.extraction.model import Page, Word
from app.services.publication import publish_artifact


def _artifact():
    # A minimal two-column spec page: column header + centre gutter (×5).
    words = [Word("1", 0.28, 0.045, 0.30, 0.062), Word("2", 0.70, 0.045, 0.72, 0.062)]
    for i in range(10):
        cy = 0.11 + 0.025 * i
        y0, y1 = cy - 0.008, cy + 0.008
        words += [
            Word("The", 0.12, y0, 0.30, y1),
            Word("housing", 0.32, y0, 0.44, y1),
            Word("right", 0.55, y0, 0.72, y1),
        ]
        if (i + 1) % 5 == 0:
            words.append(Word(str(i + 1), 0.49, y0, 0.51, y1))
    return extract_from_pages([Page(0, words)], DEFAULT_CONFIG, source_sha256="abc")


async def _seed(client) -> tuple[str, str, str]:
    async with client.sessionmaker() as s:
        user = User(subject=uuid.uuid4().hex, email="o@example.com")
        s.add(user)
        await s.flush()
        source = SourceDocument(owner_id=user.id, source_type="upload", state="uploaded")
        s.add(source)
        await s.flush()
        doc = UserDocument(owner_id=user.id, source_id=source.id, title="t", state="processing")
        s.add(doc)
        await s.commit()
        return doc.id, source.id, user.id


async def test_publish_writes_blob_and_activates(client):
    doc_id, source_id, owner_id = await _seed(client)
    art = _artifact()

    async with client.sessionmaker() as s:
        art_id = await publish_artifact(
            s,
            client.object_store,
            document_id=doc_id,
            source_id=source_id,
            owner_id=owner_id,
            artifact=art,
        )

    # The immutable entries blob round-trips.
    assert len(client.object_store._objects) == 1
    key = f"artifacts/{owner_id}/{art_id}.json.gz"
    payload = json.loads(gzip.decompress(await client.object_store.read(key, limit=10_000_000)))
    assert payload["doc_type"] == "grant"
    assert payload["entries"][0]["locator"] == {"column": 1, "printed_line": 1, "kind": "grant"}

    # DB: artifact active; document points at it and is ready.
    async with client.sessionmaker() as s:
        record = await s.get(ExtractionArtifact, art_id)
        doc = await s.get(UserDocument, doc_id)
    assert record.is_active is True
    assert doc.active_artifact_id == art_id
    assert doc.state in ("ready", "ready_with_warnings")


async def test_reprocess_demotes_prior_active(client):
    doc_id, source_id, owner_id = await _seed(client)
    async with client.sessionmaker() as s:
        first = await publish_artifact(
            s, client.object_store, document_id=doc_id, source_id=source_id,
            owner_id=owner_id, artifact=_artifact(),
        )
    async with client.sessionmaker() as s:
        second = await publish_artifact(
            s, client.object_store, document_id=doc_id, source_id=source_id,
            owner_id=owner_id, artifact=_artifact(),
        )

    async with client.sessionmaker() as s:
        old = await s.get(ExtractionArtifact, first)
        new = await s.get(ExtractionArtifact, second)
        doc = await s.get(UserDocument, doc_id)
    assert old.is_active is False and old.is_rollback is True
    assert new.is_active is True
    assert doc.active_artifact_id == second
