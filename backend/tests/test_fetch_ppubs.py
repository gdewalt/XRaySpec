"""USPTO PPUBS session, lookup, text, and PDF retrieval."""

from __future__ import annotations

import json

import httpx

from app.fetch.adapter import RestrictedFetcher
from app.fetch.ppubs import PpubsClient
from app.patents import parse_patent_identifier

ALLOWED = ["ppubs.uspto.gov", "image-ppubs.uspto.gov"]


def _public(_host):
    return ["151.207.240.15"]


async def test_ppubs_resolves_and_fetches_text_and_pdf():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/users/me/session"):
            assert request.method == "POST" and request.content == b"-1"
            return httpx.Response(200, json={}, headers={"x-access-token": "session-token"})
        if request.url.path.endswith("/searches/generic"):
            payload = json.loads(request.content)
            assert payload["q"] == "(7840427).pn."
            assert request.headers["x-access-token"] == "session-token"
            return httpx.Response(
                200,
                json={
                    "numFound": 1,
                    "docs": [
                        {
                            "documentId": "US-7840427-B2",
                            "patentNumber": "7840427",
                            "title": "Shared transport system",
                            "pageCount": 48,
                            "type": "USPAT",
                        }
                    ],
                },
            )
        if "/patents/html/" in request.url.path:
            assert request.url.params["source"] == "USPAT"
            return httpx.Response(200, text="<html>authoritative text</html>")
        if "/pdf/downloadPdf/" in request.url.path:
            return httpx.Response(200, content=b"%PDF-1.7 official image")
        raise AssertionError(f"unexpected request: {request.url}")

    fetcher = RestrictedFetcher(
        ALLOWED, resolve=_public, transport=httpx.MockTransport(handler)
    )
    client = PpubsClient(fetcher, max_json_bytes=100_000)
    document = await client.resolve(parse_patent_identifier("US7840427B2"))
    text = await client.fetch_text(document, max_bytes=100_000)
    pdf = await client.fetch_pdf(document, max_bytes=100_000)

    assert document.document_id == "US-7840427-B2"
    assert document.title == "Shared transport system"
    assert b"authoritative text" in text.content
    assert pdf.content.startswith(b"%PDF-")
    assert sum(request.url.path.endswith("/users/me/session") for request in requests) == 1
