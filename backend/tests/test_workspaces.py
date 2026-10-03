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
