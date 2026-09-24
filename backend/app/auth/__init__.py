"""Supabase JWT verification (DESIGN.md §17.1, §25.2).

The FastAPI service verifies Supabase-issued JWTs itself and maps the token
subject to a local ``User``. This is deliberately *not* the Supabase
client-direct / RLS / PostgREST pattern — the API remains the sole authority and
every query carries a server-side owner predicate.

This slice verifies HS256 tokens signed with the project's JWT secret (the
common Supabase setup, and what the tests mint). If a project uses asymmetric
JWTs, swap the verification for JWKS/RS256 here — the rest of the auth flow is
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import HTTPException, status


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """The verified caller. ``subject`` is the Supabase auth ``sub`` claim."""

    subject: str
    email: str | None = None


def verify_bearer_token(
    token: str,
    secret: str,
    *,
    audience: str = "authenticated",
    project_url: str = "",
) -> AuthenticatedUser:
    """Verify a Supabase JWT (HS256) and return the authenticated user.

    Raises 401 on any verification failure (bad signature, expired, wrong
    audience, missing subject).
    """
    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
        if algorithm == "HS256":
            if not secret:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Auth is not configured (missing JWT secret).",
                )
            claims = jwt.decode(token, secret, algorithms=["HS256"], audience=audience)
        elif algorithm in {"ES256", "RS256"} and project_url:
            signing_key = _jwks_client(project_url).get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, signing_key, algorithms=[algorithm], audience=audience)
        else:
            raise jwt.InvalidAlgorithmError("unsupported JWT signing algorithm")
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc

    subject = claims.get("sub")
    if not subject:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing subject")
    return AuthenticatedUser(subject=subject, email=claims.get("email"))


@lru_cache
def _jwks_client(project_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{project_url.rstrip('/')}/auth/v1/.well-known/jwks.json")
