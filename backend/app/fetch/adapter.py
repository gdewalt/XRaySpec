"""Restricted outbound fetcher (DESIGN.md §11.2, §17.4).

Follows redirects *manually* so the SSRF guard runs on every hop, and enforces
byte, redirect, and time ceilings. Only this adapter (used by the enrichment/fetch
worker) has outbound access.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from .errors import FetchError
from .guard import Resolver, default_resolver, validate_hop

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_STATUS_CODES = {403: "forbidden", 404: "not_found", 429: "rate_limited"}
_PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True, slots=True)
class FetchResult:
    content: bytes
    content_type: str | None
    final_url: str


class RestrictedFetcher:
    def __init__(
        self,
        allowed_hosts: list[str],
        *,
        resolve: Resolver = default_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        max_redirects: int = 5,
        connect_timeout: float = 5.0,
        read_timeout: float = 15.0,
        total_timeout: float = 30.0,
    ) -> None:
        self._allowed = {h.lower() for h in allowed_hosts}
        self._resolve = resolve
        self._transport = transport
        self._max_redirects = max_redirects
        self._timeout = httpx.Timeout(total_timeout, connect=connect_timeout, read=read_timeout)

    async def fetch(self, url: str, *, max_bytes: int, expect_pdf: bool = False) -> FetchResult:
        current = url
        for _ in range(self._max_redirects + 1):
            validate_hop(current, self._allowed, self._resolve)  # SSRF guard, every hop
            redirect_to: str | None = None
            async with httpx.AsyncClient(
                follow_redirects=False, transport=self._transport, timeout=self._timeout
            ) as client:
                try:
                    async with client.stream("GET", current) as resp:
                        if resp.status_code in _REDIRECT_CODES:
                            location = resp.headers.get("location")
                            if not location:
                                raise FetchError("malformed", "redirect without a Location header")
                            redirect_to = urljoin(current, location)
                        elif resp.status_code >= 400:
                            code = _STATUS_CODES.get(resp.status_code, "upstream_unavailable")
                            raise FetchError(code, f"upstream returned {resp.status_code}")
                        else:
                            chunks = bytearray()
                            async for chunk in resp.aiter_bytes():
                                chunks.extend(chunk)
                                if len(chunks) > max_bytes:
                                    raise FetchError("too_large", "response exceeds byte ceiling")
                            data = bytes(chunks)
                            if expect_pdf and _PDF_MAGIC not in data[:1024]:
                                raise FetchError("invalid_pdf", "response is not a PDF")
                            return FetchResult(data, resp.headers.get("content-type"), current)
                except httpx.TimeoutException as exc:
                    raise FetchError("timeout", "upstream timed out") from exc
                except httpx.HTTPError as exc:
                    raise FetchError("upstream_unavailable", str(exc)) from exc

            current = redirect_to  # loop to re-validate the redirect target

        raise FetchError("too_many_redirects", "redirect limit exceeded")
