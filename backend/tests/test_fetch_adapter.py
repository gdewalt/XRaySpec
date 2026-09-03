"""Restricted fetcher: redirect re-validation + caps (DESIGN.md §11.2, §17.4)."""

from __future__ import annotations

import httpx
import pytest

from app.fetch.adapter import RestrictedFetcher
from app.fetch.errors import FetchError

ALLOWED = ["patents.google.com", "patentimages.storage.googleapis.com"]


def _public(_host):
    return ["142.250.72.14"]


def _fetcher(handler, **kw):
    return RestrictedFetcher(
        ALLOWED, resolve=_public, transport=httpx.MockTransport(handler), **kw
    )


async def test_fetch_ok():
    def handler(_req):
        return httpx.Response(200, content=b"hello", headers={"content-type": "text/html"})

    res = await _fetcher(handler).fetch("https://patents.google.com/p", max_bytes=1000)
    assert res.content == b"hello"
    assert res.content_type == "text/html"


async def test_redirect_followed_and_revalidated():
    def handler(req):
        if req.url.host == "patents.google.com":
            loc = "https://patentimages.storage.googleapis.com/x.pdf"
            return httpx.Response(302, headers={"location": loc})
        return httpx.Response(200, content=b"%PDF-1.7 data")

    res = await _fetcher(handler).fetch(
        "https://patents.google.com/p", max_bytes=1000, expect_pdf=True
    )
    assert res.content.startswith(b"%PDF-")
    assert res.final_url.endswith("x.pdf")


async def test_redirect_to_forbidden_host_blocked():
    def handler(_req):
        return httpx.Response(302, headers={"location": "https://evil.example.com/x"})

    with pytest.raises(FetchError) as e:
        await _fetcher(handler).fetch("https://patents.google.com/p", max_bytes=1000)
    assert e.value.code == "forbidden_host"


async def test_too_large():
    def handler(_req):
        return httpx.Response(200, content=b"x" * 100)

    with pytest.raises(FetchError) as e:
        await _fetcher(handler).fetch("https://patents.google.com/p", max_bytes=10)
    assert e.value.code == "too_large"


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "not_found"), (403, "forbidden"), (429, "rate_limited"), (503, "upstream_unavailable")],
)
async def test_error_statuses(status, code):
    def handler(_req):
        return httpx.Response(status)

    with pytest.raises(FetchError) as e:
        await _fetcher(handler).fetch("https://patents.google.com/p", max_bytes=1000)
    assert e.value.code == code


async def test_invalid_pdf():
    def handler(_req):
        return httpx.Response(200, content=b"<html>not a pdf</html>")

    with pytest.raises(FetchError) as e:
        await _fetcher(handler).fetch(
            "https://patents.google.com/p", max_bytes=1000, expect_pdf=True
        )
    assert e.value.code == "invalid_pdf"


async def test_redirect_loop_capped():
    def handler(_req):
        return httpx.Response(302, headers={"location": "https://patents.google.com/again"})

    with pytest.raises(FetchError) as e:
        await _fetcher(handler, max_redirects=2).fetch(
            "https://patents.google.com/p", max_bytes=1000
        )
    assert e.value.code == "too_many_redirects"
