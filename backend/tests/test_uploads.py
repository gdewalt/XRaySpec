"""Ingestion (direct upload) tests (DESIGN.md §11.1)."""

from __future__ import annotations

PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


async def _grant(client, auth, token, filename="patent.pdf"):
    r = await client.post("/api/v1/uploads", headers=auth(token), json={"filename": filename})
    assert r.status_code == 201, r.text
    return r.json()


async def test_grant_then_complete_creates_document_and_job(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    grant = await _grant(client, auth, tok)

    # Simulate the browser PUT straight to private storage.
    await client.object_store.put(grant["object_key"], PDF_BYTES)

    r = await client.post(
        f"/api/v1/uploads/{grant['upload_id']}/complete",
        headers=auth(tok),
        json={"title": "My Patent"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_status"] == "queued"
    assert body["document"]["title"] == "My Patent"
    assert body["document"]["state"] == "processing"

    # The document now appears in the owner's list.
    listed = await client.get("/api/v1/documents", headers=auth(tok))
    assert body["document"]["id"] in [d["id"] for d in listed.json()["items"]]


async def test_grant_requires_auth(client):
    r = await client.post("/api/v1/uploads", json={"filename": "x.pdf"})
    assert r.status_code == 401


async def test_complete_rejects_non_pdf(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    grant = await _grant(client, auth, tok)
    await client.object_store.put(grant["object_key"], b"this is not a pdf")

    r = await client.post(f"/api/v1/uploads/{grant['upload_id']}/complete", headers=auth(tok), json={})
    assert r.status_code == 400
    assert "INVALID_PDF" in r.text


async def test_complete_without_uploaded_object_is_400(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    grant = await _grant(client, auth, tok)  # never PUT anything
    r = await client.post(f"/api/v1/uploads/{grant['upload_id']}/complete", headers=auth(tok), json={})
    assert r.status_code == 400


async def test_grant_is_one_time(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    grant = await _grant(client, auth, tok)
    await client.object_store.put(grant["object_key"], PDF_BYTES)
    first = await client.post(
        f"/api/v1/uploads/{grant['upload_id']}/complete", headers=auth(tok), json={}
    )
    assert first.status_code == 202
    second = await client.post(
        f"/api/v1/uploads/{grant['upload_id']}/complete", headers=auth(tok), json={}
    )
    assert second.status_code == 409


async def test_other_user_cannot_complete_grant(client, make_token, auth):
    owner = make_token("owner", "o@example.com")
    grant = await _grant(client, auth, owner)
    await client.object_store.put(grant["object_key"], PDF_BYTES)
    intruder = make_token("intruder", "x@example.com")
    r = await client.post(
        f"/api/v1/uploads/{grant['upload_id']}/complete", headers=auth(intruder), json={}
    )
    assert r.status_code == 404
