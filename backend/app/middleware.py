"""HTTP security middleware (DESIGN.md §17, §19.6).

Two concerns, kept independent so each is testable in isolation:

* :class:`SecurityHeadersMiddleware` stamps a strict, static set of response
  headers on every response. The web tier is a JSON + binary-PDF API (the SPA is
  built and served separately), so the CSP locks the API origin down to
  ``default-src 'none'`` with explicit allowances for the bundled frontend and
  Supabase API connections.
* :class:`RateLimitMiddleware` is a best-effort, per-process fixed-window limiter
  that protects a single instance from runaway or abusive callers. It is *not* a
  distributed quota (replicas are stateless — §20); disabled by default and
  enabled per-deployment via ``XRAY_RATE_LIMIT_PER_MINUTE``.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

# Endpoints that must stay cheap and always answerable (load balancer probes).
_RATE_LIMIT_EXEMPT: frozenset[str] = frozenset({"/live", "/ready"})


class SecurityHeadersMiddleware:
    """Add static security headers to every response.

    ``enable_hsts`` should be true only when the instance is served exclusively
    over TLS (production behind the managed edge — §20); asserting HSTS on a
    plain-HTTP dev server would pin browsers to a scheme that isn't there.
    """

    def __init__(self, app: ASGIApp, *, enable_hsts: bool = False) -> None:
        self.app = app
        self._headers: list[tuple[bytes, bytes]] = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"cross-origin-resource-policy", b"same-origin"),
            (b"permissions-policy", b"geolocation=(), microphone=(), camera=(), payment=()"),
            (
                b"content-security-policy",
                b"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                b"img-src 'self' data: blob:; font-src 'self' data:; "
                b"connect-src 'self' https://*.supabase.co wss://*.supabase.co; "
                b"worker-src 'self' blob:; frame-src 'self' blob:; manifest-src 'self'; "
                b"object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            ),
        ]
        if enable_hsts:
            self._headers.append(
                (b"strict-transport-security", b"max-age=63072000; includeSubDomains")
            )

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in self._headers:
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RateLimitMiddleware:
    """Fixed-window per-client rate limit (best-effort, single-process).

    Keyed by client IP. When ``trust_forwarded`` is set the left-most
    ``X-Forwarded-For`` hop is used instead (the client as seen by a single
    trusted reverse proxy); leave it off unless a proxy is known to set that
    header, since it is otherwise attacker-controlled.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        per_minute: int,
        trust_forwarded: bool = False,
        max_tracked: int = 4096,
    ) -> None:
        self.app = app
        self.per_minute = per_minute
        self.trust_forwarded = trust_forwarded
        self.max_tracked = max_tracked
        # key -> (window_start_epoch_minute, count); LRU-evicted to bound memory.
        self._windows: OrderedDict[str, tuple[int, int]] = OrderedDict()

    def _client_key(self, request: Request) -> str:
        if self.trust_forwarded:
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _check(self, key: str, now: float) -> bool:
        """Return True if the request is allowed; record it if so."""
        window = int(now // 60)
        start, count = self._windows.get(key, (window, 0))
        if start != window:
            start, count = window, 0
        if count >= self.per_minute:
            self._windows[key] = (start, count)
            self._windows.move_to_end(key)
            return False
        self._windows[key] = (start, count + 1)
        self._windows.move_to_end(key)
        while len(self._windows) > self.max_tracked:
            self._windows.popitem(last=False)
        return True

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self.per_minute <= 0:
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        if request.url.path in _RATE_LIMIT_EXEMPT:
            await self.app(scope, receive, send)
            return
        if self._check(self._client_key(request), time.time()):
            await self.app(scope, receive, send)
            return
        response: Response = JSONResponse(
            {"detail": "Rate limit exceeded. Retry shortly."},
            status_code=429,
            headers={"Retry-After": "60"},
        )
        await response(scope, receive, send)
