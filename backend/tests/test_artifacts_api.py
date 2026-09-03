"""Artifact reading API (DESIGN.md §14.1)."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import SourceDocument, User, UserDocument
from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import extract_from_pages
from app.extraction.model import Page, Word
from app.services.publication import publish_artifact


def _artifact():
    words = [
        Word("1", 0.03, 0.09, 0.06, 0.11),
        Word("The", 0.12, 0.09, 0.20, 0.11),
        Word("housing", 0.21, 0.09, 0.35, 0.11),
        Word("104", 0.36, 0.09, 0.42, 0.11),
        Word("2", 0.03, 0.14, 0.06, 0.16),
        Word("shown", 0.12, 0.14, 0.22, 0.16),
        Word("in", 0.23, 0.14, 0.26, 0.16),
        Word("FIG.", 0.27, 0.14, 0.34, 0.16),
        Word("3", 0.35, 0.14, 0.38, 0.16),
    ]
    return extract_from_pages([Page(0, words)], DEFAULT_CONFIG, source_sha256="abc")


async def _setup(client, auth, token, sub: str) -> tuple[str, str]:
    assert (await client.get("/api/v1/documents", headers=auth(token))).status_code == 200
    async with client.sessionmaker() as s:
        uid = await s.scalar(select(User.id).where(User.subject == sub))
        source = SourceDocument(owner_id=uid, source_type="upload", state="uploaded")
        s.add(source)
        await s.flush()
        doc = UserDocument(owner_id=uid, source_id=source.id, title="t", state="processing")
        s.add(doc)
        await s.commit()
        doc_id, source_id = doc.id, source.id
    async with client.sessionmaker() as s:
        art_id = await publish_artifact(
            s, client.object_store, document_id=doc_id, source_id=source_id,
            owner_id=uid, artifact=_artifact(),
        )
    return doc_id, art_id


async def test_get_artifact_entries(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    _doc_id, art_id = await _setup(client, auth, tok, "owner")

    r = await client.get(f"/api/v1/artifacts/{art_id}/entries", headers=auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc_type"] == "grant"
    assert len(body["entries"]) >= 2
    assert body["entries"][0]["locator"] == {"column": 1, "printed_line": 1, "kind": "grant"}
    # detection ran: "housing 104" numeral + "FIG. 3" figure reference.
    assert any(m["value"] == "104" for m in body["numeral_mentions"])
    assert any("3" in m["figure_ids"] for m in body["figure_mentions"])


async def test_get_artifact_metadata(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    _doc_id, art_id = await _setup(client, auth, tok, "owner")
    r = await client.get(f"/api/v1/artifacts/{art_id}", headers=auth(tok))
    assert r.status_code == 200
    assert r.json()["is_active"] is True


async def test_artifact_is_owner_scoped(client, make_token, auth):
    owner = make_token("owner", "o@example.com")
    _doc_id, art_id = await _setup(client, auth, owner, "owner")
    intruder = make_token("intruder", "x@example.com")
    r = await client.get(f"/api/v1/artifacts/{art_id}/entries", headers=auth(intruder))
    assert r.status_code == 404
