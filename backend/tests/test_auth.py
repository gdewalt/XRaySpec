"""Authentication gate tests (DESIGN.md §17.1, §22)."""

from __future__ import annotations


async def test_missing_token_is_401(client):
    r = await client.get("/api/v1/documents")
    assert r.status_code == 401


async def test_garbage_token_is_401(client, auth):
    r = await client.get("/api/v1/documents", headers=auth("not-a-jwt"))
    assert r.status_code == 401


async def test_valid_token_is_accepted(client, make_token, auth):
    r = await client.get("/api/v1/documents", headers=auth(make_token("sub-1", "a@example.com")))
    assert r.status_code == 200
    assert r.json() == {"items": []}


async def test_not_allowlisted_is_403(client, make_token, auth, monkeypatch):
    import app.api.deps as deps
    from app.config import Settings

    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: Settings(supabase_jwt_secret="test-secret", allowed_emails=["allowed@example.com"]),
    )
    r = await client.get("/api/v1/documents", headers=auth(make_token("s", "denied@example.com")))
    assert r.status_code == 403
