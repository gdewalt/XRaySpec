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


async def test_first_page_thumbnail_is_generated_once_and_cached(
    client, make_token, auth, monkeypatch
):
    tok = make_token("owner", "o@example.com")
    doc_id = await _upload_doc(client, auth, tok)
    renders = 0

    def fake_render(pdf: bytes) -> bytes:
        nonlocal renders
        renders += 1
        assert pdf == PDF
        return b"\x89PNG\r\npreview"

    monkeypatch.setattr("app.api.v1.documents.render_pdf_thumbnail", fake_render)
    first = await client.get(
        f"/api/v1/documents/{doc_id}/thumbnail.png", headers=auth(tok)
    )
    second = await client.get(
        f"/api/v1/documents/{doc_id}/thumbnail.png", headers=auth(tok)
    )

    assert first.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.content == second.content == b"\x89PNG\r\npreview"
    assert renders == 1


async def test_first_page_thumbnail_is_owner_scoped(client, make_token, auth):
    doc_id = await _upload_doc(client, auth, make_token("owner", "o@example.com"))
    intruder = make_token("intruder", "x@example.com")
    response = await client.get(
        f"/api/v1/documents/{doc_id}/thumbnail.png", headers=auth(intruder)
    )
    assert response.status_code == 404
