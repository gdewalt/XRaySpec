"""Supabase JWT verification (DESIGN.md §17.1, §25.2).

The FastAPI service verifies Supabase-issued JWTs itself and maps the token
subject to a local ``User``. This is deliberately *not* the Supabase
client-direct / RLS / PostgREST pattern — the API remains the sole authority and
every query carries a server-side owner predicate.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """The verified caller. ``subject`` is the Supabase auth ``sub`` claim."""

    subject: str
    email: str | None = None


def verify_bearer_token(token: str) -> AuthenticatedUser:
    """Verify a Supabase JWT and return the authenticated user.

    TODO: verify signature against ``settings.supabase_jwt_secret`` (HS256) and
    audience/expiry with a JWT library (e.g. PyJWT), then enforce the email
    allowlist (DESIGN.md §3.1). Currently a stub that always rejects.
    """
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "JWT verification not yet implemented (DESIGN.md §17.1, Phase 1).",
    )
