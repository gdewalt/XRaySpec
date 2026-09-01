"""Shared API dependencies: DB session and authenticated user (DESIGN.md §14.2, §17.1).

Identity comes from the authenticated session (a verified Supabase JWT), never
from a request body. Every resource query must additionally apply an owner
predicate before results are revealed.
"""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends, Header, HTTPException, status

from ..auth import AuthenticatedUser, verify_bearer_token
from ..db.base import async_session


async def get_db() -> AsyncIterator[object]:
    """Yield an async SQLAlchemy session."""
    async with async_session() as session:
        yield session


async def current_user(
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    """Resolve the current user from a verified Supabase JWT.

    Raises 401 if absent/invalid. Membership/allowlist checks (DESIGN.md §3.1)
    happen here once wired.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    return verify_bearer_token(token)


CurrentUser = Depends(current_user)
DbSession = Depends(get_db)
