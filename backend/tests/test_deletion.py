"""Document deletion workflow (DESIGN.md §9.4)."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.db.models import ExtractionArtifact, SourceDocument, UserDocument

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF"

IMPORT_DOC = {
    "schema_version": 2,
    "title": "To delete",
    "entries": [
        {
            "locator": {"kind": "grant", "column": 3, "printed_line": 15},
            "page_index": 6,
            "source_text": "the housing 104",
        }
    ],
    "bookmarks": [{"entry_index": 0, "label": "mark"}],
}


async def _upload_document(client, auth, tok) -> dict:
    resp = await client.post("/api/v1/uploads", headers=auth(tok), json={"filename": "p.pdf"})
    grant = resp.json()
    await client.object_store.put(grant["object_key"], PDF)
    r = await client.post(
        f"/api/v1/uploads/{grant['upload_id']}/complete", headers=auth(tok), json={}
    )
    return r.json()["document"]


async def _import_document(client, auth, tok) -> dict:
    resp = await client.post(
        "/api/v1/imports", headers=auth(tok), content=json.dumps(IMPORT_DOC).encode()
    )
    analysis = resp.json()
    r = await client.post(
        f"/api/v1/imports/{analysis['import_id']}/commit", headers=auth(tok), json={}
    )
    return r.json()


async def test_delete_upload_removes_pdf_blob(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc = await _upload_document(client, auth, tok)
    assert len(client.object_store._objects) == 1

    r = await client.delete(f"/api/v1/documents/{doc['id']}", headers=auth(tok))
    assert r.status_code == 204
    assert len(client.object_store._objects) == 0  # blob removed
    gone = await client.get(f"/api/v1/documents/{doc['id']}", headers=auth(tok))
    assert gone.status_code == 404


async def test_delete_import_removes_artifact_blob_and_bookmarks(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc = await _import_document(client, auth, tok)
    assert len(client.object_store._objects) == 1
    bms = await client.get(f"/api/v1/documents/{doc['id']}/bookmarks", headers=auth(tok))
    assert len(bms.json()) == 1

    r = await client.delete(f"/api/v1/documents/{doc['id']}", headers=auth(tok))
    assert r.status_code == 204
    assert len(client.object_store._objects) == 0
    # Document (and thus its bookmarks) are gone.
    gone = await client.get(f"/api/v1/documents/{doc['id']}", headers=auth(tok))
    assert gone.status_code == 404


async def test_delete_excludes_from_list(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc = await _upload_document(client, auth, tok)
    await client.delete(f"/api/v1/documents/{doc['id']}", headers=auth(tok))
    listed = await client.get("/api/v1/documents", headers=auth(tok))
    assert doc["id"] not in [d["id"] for d in listed.json()["items"]]


async def test_second_delete_returns_404(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc = await _upload_document(client, auth, tok)
    first = await client.delete(f"/api/v1/documents/{doc['id']}", headers=auth(tok))
    assert first.status_code == 204
    second = await client.delete(f"/api/v1/documents/{doc['id']}", headers=auth(tok))
    assert second.status_code == 404


async def test_other_user_cannot_delete(client, make_token, auth):
    doc = await _upload_document(client, auth, make_token("owner", "o@example.com"))
    intruder = make_token("intruder", "x@example.com")
    r = await client.delete(f"/api/v1/documents/{doc['id']}", headers=auth(intruder))
    assert r.status_code == 404
    # The owner's blob is untouched.
    assert len(client.object_store._objects) == 1


async def test_delete_preserves_a_source_shared_by_another_document(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    first = await _import_document(client, auth, tok)

    async with client.sessionmaker() as session:
        original = await session.get(UserDocument, first["id"])
        assert original is not None
        duplicate = UserDocument(
            owner_id=original.owner_id,
            source_id=original.source_id,
            active_artifact_id=original.active_artifact_id,
            title="Shared copy",
            state="ready",
        )
        session.add(duplicate)
        await session.commit()
        duplicate_id = duplicate.id
        source_id = original.source_id
        artifact_id = original.active_artifact_id

    response = await client.delete(f"/api/v1/documents/{first['id']}", headers=auth(tok))
    assert response.status_code == 204
    assert len(client.object_store._objects) == 1

    async with client.sessionmaker() as session:
        assert await session.get(UserDocument, duplicate_id) is not None
        assert await session.get(SourceDocument, source_id) is not None
        assert await session.get(ExtractionArtifact, artifact_id) is not None
        remaining = await session.scalar(
            select(UserDocument).where(UserDocument.id == duplicate_id)
        )
        assert remaining is not None
