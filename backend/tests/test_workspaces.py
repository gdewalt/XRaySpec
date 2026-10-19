"""Owner-scoped patent workspace behavior."""

from __future__ import annotations


async def _create_doc(client, auth, token, title: str = "Patent") -> str:
    response = await client.post(
        "/api/v1/documents",
        headers=auth(token),
        json={"title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_workspace(client, auth, token, name: str = "Portfolio") -> dict:
    response = await client.post(
        "/api/v1/workspaces",
        headers=auth(token),
        json={"name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_and_list_workspaces_are_owner_scoped(client, make_token, auth):
    owner = make_token("owner", "owner@example.com")
    other = make_token("other", "other@example.com")

    first = await _create_workspace(client, auth, owner, "  Battery   Cooling  ")
    await _create_workspace(client, auth, other, "Private")

    response = await client.get("/api/v1/workspaces", headers=auth(owner))
    assert response.status_code == 200
    assert response.json()["items"] == [first]
    assert first["name"] == "Battery Cooling"


async def test_duplicate_workspace_name_is_rejected(client, make_token, auth):
    token = make_token("owner", "owner@example.com")
    await _create_workspace(client, auth, token, "Portfolio")

    duplicate = await client.post(
        "/api/v1/workspaces",
        headers=auth(token),
        json={"name": "Portfolio"},
    )
    assert duplicate.status_code == 409


async def test_workspace_can_be_renamed_but_not_to_a_duplicate(client, make_token, auth):
    token = make_token("owner", "owner@example.com")
    workspace = await _create_workspace(client, auth, token, "Portfolio")
    await _create_workspace(client, auth, token, "Prior Art")

    renamed = await client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        headers=auth(token),
        json={"name": "  Active   Matter  "},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Active Matter"

    duplicate = await client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        headers=auth(token),
        json={"name": "Prior Art"},
    )
    assert duplicate.status_code == 409


async def test_patent_can_move_between_workspace_and_unfiled(client, make_token, auth):
    token = make_token("owner", "owner@example.com")
    document_id = await _create_doc(client, auth, token)
    workspace = await _create_workspace(client, auth, token)

    moved = await client.patch(
        f"/api/v1/documents/{document_id}/workspace",
        headers=auth(token),
        json={"workspace_id": workspace["id"]},
    )
    assert moved.status_code == 200
    assert moved.json()["workspace_id"] == workspace["id"]

    unfiled = await client.patch(
        f"/api/v1/documents/{document_id}/workspace",
        headers=auth(token),
        json={"workspace_id": None},
    )
    assert unfiled.status_code == 200
    assert unfiled.json()["workspace_id"] is None


async def test_patent_cannot_move_to_another_users_workspace(client, make_token, auth):
    owner = make_token("owner", "owner@example.com")
    intruder = make_token("intruder", "intruder@example.com")
    document_id = await _create_doc(client, auth, owner)
    foreign_workspace = await _create_workspace(client, auth, intruder)

    response = await client.patch(
        f"/api/v1/documents/{document_id}/workspace",
        headers=auth(owner),
        json={"workspace_id": foreign_workspace["id"]},
    )
    assert response.status_code == 404

    document = await client.get(
        f"/api/v1/documents/{document_id}",
        headers=auth(owner),
    )
    assert document.json()["workspace_id"] is None


async def test_deleting_workspace_moves_patents_to_unfiled(client, make_token, auth):
    token = make_token("owner", "owner@example.com")
    document_id = await _create_doc(client, auth, token)
    workspace = await _create_workspace(client, auth, token)
    moved = await client.patch(
        f"/api/v1/documents/{document_id}/workspace",
        headers=auth(token),
        json={"workspace_id": workspace["id"]},
    )
    assert moved.status_code == 200

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace['id']}", headers=auth(token)
    )
    assert deleted.status_code == 204

    document = await client.get(f"/api/v1/documents/{document_id}", headers=auth(token))
    assert document.json()["workspace_id"] is None
    listed = await client.get("/api/v1/workspaces", headers=auth(token))
    assert listed.json()["items"] == []


async def test_workspace_changes_are_owner_scoped(client, make_token, auth):
    owner = make_token("owner", "owner@example.com")
    intruder = make_token("intruder", "intruder@example.com")
    workspace = await _create_workspace(client, auth, owner)

    renamed = await client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        headers=auth(intruder),
        json={"name": "Stolen"},
    )
    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace['id']}", headers=auth(intruder)
    )
    assert renamed.status_code == 404
    assert deleted.status_code == 404


async def test_patents_can_be_reordered_and_moved_by_placement(client, make_token, auth):
    token = make_token("owner", "owner@example.com")
    first = await _create_doc(client, auth, token, "First")
    second = await _create_doc(client, auth, token, "Second")
    third = await _create_doc(client, auth, token, "Third")
    workspace = await _create_workspace(client, auth, token)

    move_first = await client.patch(
        f"/api/v1/documents/{first}/placement",
        headers=auth(token),
        json={"workspace_id": workspace["id"], "before_document_id": None},
    )
    move_second = await client.patch(
        f"/api/v1/documents/{second}/placement",
        headers=auth(token),
        json={"workspace_id": workspace["id"], "before_document_id": first},
    )
    assert move_first.status_code == 200
    assert move_second.status_code == 200

    listed = await client.get("/api/v1/documents", headers=auth(token))
    workspace_ids = [
        item["id"]
        for item in listed.json()["items"]
        if item["workspace_id"] == workspace["id"]
    ]
    assert workspace_ids == [second, first]

    reorder = await client.patch(
        f"/api/v1/documents/{first}/placement",
        headers=auth(token),
        json={"workspace_id": workspace["id"], "before_document_id": second},
    )
    assert reorder.status_code == 200
    listed = await client.get("/api/v1/documents", headers=auth(token))
    workspace_ids = [
        item["id"]
        for item in listed.json()["items"]
        if item["workspace_id"] == workspace["id"]
    ]
    assert workspace_ids == [first, second]

    invalid = await client.patch(
        f"/api/v1/documents/{third}/placement",
        headers=auth(token),
        json={"workspace_id": workspace["id"], "before_document_id": third},
    )
    assert invalid.status_code == 400
