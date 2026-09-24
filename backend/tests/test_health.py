"""Liveness / readiness probes and headers on the real app (DESIGN.md §18.1)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_live(client):
    res = await client.get("/live")
    assert res.status_code == 200
    assert res.json()["status"] == "live"


async def test_ready_ok_when_backends_reachable(client):
    # The client fixture overrides the DB (SQLite) and store (memory), so both
    # readiness checks succeed.
    res = await client.get("/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    assert body["storage"] == "ok"


async def test_security_headers_on_real_app(client):
    res = await client.get("/live")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in res.headers["content-security-policy"]
    # Dev/test environment: HSTS must not be asserted over plain HTTP.
    assert "strict-transport-security" not in res.headers


async def test_runtime_config_is_javascript_and_exposes_no_server_secrets(client):
    res = await client.get("/runtime-config.js")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/javascript")
    assert res.headers["cache-control"] == "no-store"
    assert "supabaseUrl" in res.text
    assert "supabaseAnonKey" in res.text
    assert "service" not in res.text.lower()
    assert "jwt" not in res.text.lower()
