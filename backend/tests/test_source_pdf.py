"""Authorized source-PDF delivery (DESIGN.md §14.1, §17.5)."""

from __future__ import annotations

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF"


async def _upload_doc(client, auth, token) -> str:
    grant = (
        await client.post("/api/v1/uploads", headers=auth(token), json={"filename": "p.pdf"})
    ).json()
    await client.object_store.put(grant["object_key"], PDF)
    r = await client.post(
        f"/api/v1/uploads/{grant['upload_id']}/complete", headers=auth(token), json={}
    )
    return r.json()["document"]["id"]


async def test_source_pdf_served(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc_id = await _upload_doc(client, auth, tok)
    r = await client.get(f"/api/v1/documents/{doc_id}/source.pdf", headers=auth(tok))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")


async def test_source_pdf_404_without_pdf(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    r = await client.post(
        "/api/v1/documents",
        headers=auth(tok),
        json={"source_type": "fetch", "patent_identifier": "US12262260B2"},
    )
    doc_id = r.json()["id"]  # a fetch doc has no PDF yet
    got = await client.get(f"/api/v1/documents/{doc_id}/source.pdf", headers=auth(tok))
    assert got.status_code == 404


async def test_source_pdf_is_owner_scoped(client, make_token, auth):
    doc_id = await _upload_doc(client, auth, make_token("owner", "o@example.com"))
    intruder = make_token("intruder", "x@example.com")
    r = await client.get(f"/api/v1/documents/{doc_id}/source.pdf", headers=auth(intruder))
    assert r.status_code == 404
