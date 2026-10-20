"""Production-shaped Supabase Storage adapter tests."""

from __future__ import annotations

import httpx
import pytest

from app.storage.supabase import SupabaseObjectStore


async def test_read_translates_supabase_missing_object_400():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(
            "/storage/v1/object/xray-sources/artifacts/user/missing.json.gz"
        )
        return httpx.Response(
            400,
            json={"statusCode": "404", "error": "not_found", "message": "Object not found"},
        )

    store = SupabaseObjectStore(
        "https://example.supabase.co",
        "service-key",
        "xray-sources",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(FileNotFoundError):
        await store.read("artifacts/user/missing.json.gz", limit=1024)


async def test_delete_uses_supabase_remove_endpoint():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    store = SupabaseObjectStore(
        "https://example.supabase.co",
        "service-key",
        "xray-sources",
        transport=httpx.MockTransport(handler),
    )
    await store.delete("artifacts/user/artifact.json.gz")

    assert len(seen) == 1
    assert seen[0].method == "DELETE"
    assert seen[0].url.path.endswith("/storage/v1/object/xray-sources")
    assert seen[0].read() == b'{"prefixes":["artifacts/user/artifact.json.gz"]}'


async def test_read_preserves_unrelated_bad_request():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Invalid request"})

    store = SupabaseObjectStore(
        "https://example.supabase.co",
        "service-key",
        "xray-sources",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await store.read("artifact.json.gz", limit=1024)
