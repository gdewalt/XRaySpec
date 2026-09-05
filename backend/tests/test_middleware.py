"""Security-header and rate-limit middleware (DESIGN.md §17, §19.6)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.middleware import RateLimitMiddleware, SecurityHeadersMiddleware

pytestmark = pytest.mark.asyncio


def _app(**kwargs) -> Starlette:
    async def ok(_request):
        return PlainTextResponse("ok")

    async def live(_request):
        return PlainTextResponse("live")

    star = Starlette(routes=[Route("/x", ok), Route("/live", live)])
    if "hsts" in kwargs:
        star.add_middleware(SecurityHeadersMiddleware, enable_hsts=kwargs["hsts"])
    if "per_minute" in kwargs:
        star.add_middleware(RateLimitMiddleware, per_minute=kwargs["per_minute"])
    return star


async def _get(app, path="/x", headers=None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get(path, headers=headers)


async def test_security_headers_present():
    res = await _get(_app(hsts=False))
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["cross-origin-opener-policy"] == "same-origin"
    assert "default-src 'none'" in res.headers["content-security-policy"]
    assert "strict-transport-security" not in res.headers


async def test_hsts_only_when_enabled():
    res = await _get(_app(hsts=True))
    assert "max-age=" in res.headers["strict-transport-security"]


async def test_rate_limit_blocks_after_budget():
    app = _app(per_minute=2)
    assert (await _get(app)).status_code == 200
    assert (await _get(app)).status_code == 200
    blocked = await _get(app)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"


async def test_rate_limit_disabled_when_zero():
    app = _app(per_minute=0)
    for _ in range(5):
        assert (await _get(app)).status_code == 200


async def test_rate_limit_exempts_health():
    app = _app(per_minute=1)
    assert (await _get(app, "/live")).status_code == 200
    assert (await _get(app, "/live")).status_code == 200  # not counted


async def test_rate_limit_keys_per_client():
    app = _app(per_minute=1)
    a = {"x-forwarded-for": "203.0.113.1"}
    # trust_forwarded defaults off, so all requests share the ASGI client host key.
    assert (await _get(app, headers=a)).status_code == 200
    assert (await _get(app, headers=a)).status_code == 429


async def test_security_headers_stamped_on_rate_limited_response():
    # Compose in the same order as app.main: rate limiter added first, security
    # headers added last (outermost) so a 429 still carries the headers.
    app = _app()
    app.add_middleware(RateLimitMiddleware, per_minute=1)
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=False)
    assert (await _get(app)).status_code == 200
    blocked = await _get(app)
    assert blocked.status_code == 429
    assert blocked.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in blocked.headers["content-security-policy"]
