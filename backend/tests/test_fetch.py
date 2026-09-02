"""Fetch-by-identifier document creation (DESIGN.md §11.2, §14.1)."""

from __future__ import annotations


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
    assert body["state"] == "processing"

    # It shows up in the owner's list.
    listed = await client.get("/api/v1/documents", headers=auth(tok))
    assert body["id"] in [d["id"] for d in listed.json()["items"]]


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
    r = await client.post(
        "/api/v1/documents", headers=auth(tok), json={"source_type": "fetch"}
    )
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
