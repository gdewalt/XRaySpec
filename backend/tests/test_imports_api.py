"""Portable import API: analyze -> commit (DESIGN.md §11.3, §14.1)."""

from __future__ import annotations

import json

V2 = {
    "schema_version": 2,
    "title": "Imported widget",
    "entries": [
        {
            "locator": {"kind": "grant", "column": 3, "printed_line": 15},
            "page_index": 6,
            "source_text": "the housing 104",
        }
    ],
    "bookmarks": [{"entry_index": 0, "label": "claim start"}],
}

V1 = {
    "schema_version": 1,
    "entries": [{"ref": "3:15", "page_index": 6, "source_text": "legacy line"}],
}


async def _analyze(client, auth, token, doc) -> dict:
    r = await client.post(
        "/api/v1/imports", headers=auth(token), content=json.dumps(doc).encode()
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_analyze_then_commit_creates_text_only_document(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    analysis = await _analyze(client, auth, tok, V2)
    assert analysis["entry_count"] == 1
    assert analysis["bookmark_count"] == 1
    assert analysis["imported_unverified"] is True
    assert analysis["needs_migration"] is False

    r = await client.post(
        f"/api/v1/imports/{analysis['import_id']}/commit", headers=auth(tok), json={}
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["state"] == "text_only"
    assert doc["title"] == "Imported widget"

    listed = await client.get("/api/v1/documents", headers=auth(tok))
    assert doc["id"] in [d["id"] for d in listed.json()["items"]]

    bookmarks = await client.get(f"/api/v1/documents/{doc['id']}/bookmarks", headers=auth(tok))
    assert [b["entry_id"] for b in bookmarks.json()] == ["line_0000000"]


async def test_v1_requires_migration_confirmation(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    analysis = await _analyze(client, auth, tok, V1)
    assert analysis["needs_migration"] is True

    without = await client.post(
        f"/api/v1/imports/{analysis['import_id']}/commit", headers=auth(tok), json={}
    )
    assert without.status_code == 400
    assert "migration_confirmation_required" in without.text

    with_confirm = await client.post(
        f"/api/v1/imports/{analysis['import_id']}/commit",
        headers=auth(tok),
        json={"confirm_migration": True},
    )
    assert with_confirm.status_code == 201


async def test_analyze_rejects_bad_json(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    r = await client.post("/api/v1/imports", headers=auth(tok), content=b"not json at all")
    assert r.status_code == 400
    assert "invalid_json" in r.text


async def test_commit_unknown_import_is_404(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    r = await client.post("/api/v1/imports/imp_missing/commit", headers=auth(tok), json={})
    assert r.status_code == 404


async def test_other_user_cannot_commit(client, make_token, auth):
    analysis = await _analyze(client, auth, make_token("owner", "o@example.com"), V2)
    r = await client.post(
        f"/api/v1/imports/{analysis['import_id']}/commit",
        headers=auth(make_token("intruder", "x@example.com")),
        json={},
    )
    assert r.status_code == 404


async def test_commit_is_one_time(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    analysis = await _analyze(client, auth, tok, V2)
    first = await client.post(
        f"/api/v1/imports/{analysis['import_id']}/commit", headers=auth(tok), json={}
    )
    assert first.status_code == 201
    second = await client.post(
        f"/api/v1/imports/{analysis['import_id']}/commit", headers=auth(tok), json={}
    )
    assert second.status_code == 409


async def test_import_requires_auth(client):
    r = await client.post("/api/v1/imports", content=json.dumps(V2).encode())
    assert r.status_code == 401
