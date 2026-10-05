"""Per-user authorization (IDOR) matrix (DESIGN.md §22 — zero cross-user access).

Unauthorized access to another user's resource returns 404, not 403 (§14.2).
"""

from __future__ import annotations


async def _create_doc(client, auth, token, title="Doc") -> str:
    r = await client.post("/api/v1/documents", headers=auth(token), json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_owner_can_read_own_document(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc_id = await _create_doc(client, auth, tok)
    r = await client.get(f"/api/v1/documents/{doc_id}", headers=auth(tok))
    assert r.status_code == 200
    assert r.json()["id"] == doc_id


async def test_other_user_cannot_read_document(client, make_token, auth):
    doc_id = await _create_doc(client, auth, make_token("owner", "o@example.com"))
    r = await client.get(
        f"/api/v1/documents/{doc_id}", headers=auth(make_token("intruder", "x@example.com"))
    )
    assert r.status_code == 404


async def test_other_user_cannot_delete_document(client, make_token, auth):
    doc_id = await _create_doc(client, auth, make_token("owner", "o@example.com"))
    r = await client.delete(
        f"/api/v1/documents/{doc_id}", headers=auth(make_token("intruder", "x@example.com"))
    )
    assert r.status_code == 404


async def test_list_is_scoped_to_owner(client, make_token, auth):
    owner = make_token("owner", "o@example.com")
    other = make_token("other", "y@example.com")
    await _create_doc(client, auth, owner, "A")
    await _create_doc(client, auth, owner, "B")
    await _create_doc(client, auth, other, "C")

    r_owner = await client.get("/api/v1/documents", headers=auth(owner))
    r_other = await client.get("/api/v1/documents", headers=auth(other))
    assert len(r_owner.json()["items"]) == 2
    assert len(r_other.json()["items"]) == 1


async def test_delete_revokes_access(client, make_token, auth):
    tok = make_token("owner", "o@example.com")
    doc_id = await _create_doc(client, auth, tok)
    deleted = await client.delete(f"/api/v1/documents/{doc_id}", headers=auth(tok))
    assert deleted.status_code == 204
    # Now invisible to its former owner too.
    gone = await client.get(f"/api/v1/documents/{doc_id}", headers=auth(tok))
    assert gone.status_code == 404


async def test_bookmark_authorization(client, make_token, auth):
    owner = make_token("owner", "o@example.com")
    intruder = make_token("intruder", "x@example.com")
    doc_id = await _create_doc(client, auth, owner)

    created = await client.post(
        f"/api/v1/documents/{doc_id}/bookmarks",
        headers=auth(owner),
        json={"entry_id": "line_0000123", "label": "claim 1"},
    )
    assert created.status_code == 201
    bookmark_id = created.json()["id"]

    # Intruder cannot create on, list, or delete against the owner's document/bookmark.
    assert (
        await client.post(
            f"/api/v1/documents/{doc_id}/bookmarks",
            headers=auth(intruder),
            json={"entry_id": "line_1", "label": "x"},
        )
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/documents/{doc_id}/bookmarks", headers=auth(intruder))
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/bookmarks/{bookmark_id}", headers=auth(intruder))
    ).status_code == 404

    # Owner sees exactly their one bookmark.
    listed = await client.get(f"/api/v1/documents/{doc_id}/bookmarks", headers=auth(owner))
    assert [b["id"] for b in listed.json()] == [bookmark_id]


async def test_annotation_authorization(client, make_token, auth):
    owner = make_token("owner", "o@example.com")
    intruder = make_token("intruder", "x@example.com")
    doc_id = await _create_doc(client, auth, owner)

    created = await client.post(
        f"/api/v1/documents/{doc_id}/annotations",
        headers=auth(owner),
        json={"target_entry_id": "line_0000123", "note": "check this claim"},
    )
    assert created.status_code == 201
    annotation_id = created.json()["id"]

    # Intruder cannot create on, list, update, or delete the owner's annotation.
    assert (
        await client.post(
            f"/api/v1/documents/{doc_id}/annotations",
            headers=auth(intruder),
            json={"target_entry_id": "line_1", "note": "x"},
        )
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/documents/{doc_id}/annotations", headers=auth(intruder))
    ).status_code == 404
    assert (
        await client.patch(
            f"/api/v1/annotations/{annotation_id}",
            headers=auth(intruder),
            json={"note": "tampered"},
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/annotations/{annotation_id}", headers=auth(intruder))
    ).status_code == 404

    updated = await client.patch(
        f"/api/v1/annotations/{annotation_id}",
        headers=auth(owner),
        json={"note": "reviewed claim"},
    )
    assert updated.status_code == 200
    assert updated.json()["note"] == "reviewed claim"

    listed = await client.get(f"/api/v1/documents/{doc_id}/annotations", headers=auth(owner))
    assert [a["id"] for a in listed.json()] == [annotation_id]

    # Owner can delete their own annotation.
    assert (
        await client.delete(f"/api/v1/annotations/{annotation_id}", headers=auth(owner))
    ).status_code == 204


async def test_pdf_annotation_persistence_and_authorization(client, make_token, auth):
    owner = make_token("owner", "o@example.com")
    intruder = make_token("intruder", "x@example.com")
    doc_id = await _create_doc(client, auth, owner)
    body = {
        "kind": "highlight",
        "page_index": 2,
        "geometry": {"x0": 0.1, "y0": 0.2, "x1": 0.6, "y1": 0.25},
        "color": "#ffe066",
    }

    created = await client.post(
        f"/api/v1/documents/{doc_id}/pdf-annotations", headers=auth(owner), json=body
    )
    assert created.status_code == 201, created.text
    annotation_id = created.json()["id"]

    assert (
        await client.get(
            f"/api/v1/documents/{doc_id}/pdf-annotations", headers=auth(intruder)
        )
    ).status_code == 404
    assert (
        await client.delete(
            f"/api/v1/pdf-annotations/{annotation_id}", headers=auth(intruder)
        )
    ).status_code == 404

    listed = await client.get(
        f"/api/v1/documents/{doc_id}/pdf-annotations", headers=auth(owner)
    )
    assert [annotation["id"] for annotation in listed.json()] == [annotation_id]
    assert (
        await client.delete(
            f"/api/v1/pdf-annotations/{annotation_id}", headers=auth(owner)
        )
    ).status_code == 204


async def test_override_authorization(client, make_token, auth):
    owner = make_token("owner", "o@example.com")
    intruder = make_token("intruder", "x@example.com")
    doc_id = await _create_doc(client, auth, owner)

    body = {"entry_id": "line_0000005", "span_start": 4, "span_end": 7, "callout_id": "callout_1"}
    created = await client.put(
        f"/api/v1/documents/{doc_id}/overrides", headers=auth(owner), json=body
    )
    assert created.status_code == 200
    override_id = created.json()["id"]

    # Upsert replaces the choice for the same mention (no duplicate row).
    again = await client.put(
        f"/api/v1/documents/{doc_id}/overrides",
        headers=auth(owner),
        json={**body, "callout_id": None},
    )
    assert again.status_code == 200 and again.json()["id"] == override_id
    assert again.json()["callout_id"] is None

    listed = await client.get(f"/api/v1/documents/{doc_id}/overrides", headers=auth(owner))
    assert [o["id"] for o in listed.json()] == [override_id]

    # Intruder cannot upsert, list, or delete.
    assert (
        await client.put(f"/api/v1/documents/{doc_id}/overrides", headers=auth(intruder), json=body)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/documents/{doc_id}/overrides", headers=auth(intruder))
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/overrides/{override_id}", headers=auth(intruder))
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/overrides/{override_id}", headers=auth(owner))
    ).status_code == 204
