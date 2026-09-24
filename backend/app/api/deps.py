"""Shared API dependencies: DB session and authenticated user (DESIGN.md §14.2, §17.1).

Identity comes from the authenticated session (a verified Supabase JWT), never
from a request body. Every resource query additionally applies an owner
predicate before results are revealed (see ``app.api.v1.documents``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthenticatedUser, verify_bearer_token
from ..config import get_settings
from ..db.base import get_sessionmaker
from ..db.models import User
from ..storage.base import ObjectStore


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield one async SQLAlchemy session per request."""
    async with get_sessionmaker()() as session:
        yield session


async def _get_or_create_user(session: AsyncSession, auth: AuthenticatedUser) -> User:
    existing = await session.scalar(select(User).where(User.subject == auth.subject))
    if existing is not None:
        return existing
    user = User(subject=auth.subject, email=auth.email)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:  # concurrent first-login race
        await session.rollback()
        user = await session.scalar(select(User).where(User.subject == auth.subject))
        if user is None:  # pragma: no cover - should not happen
            raise
    await session.refresh(user)
    return user


async def current_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the current user from a verified Supabase JWT.

    401 if the token is missing/invalid; 403 if the caller's email is not on the
    instance allowlist (DESIGN.md §3.1).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    settings = get_settings()
    auth = await asyncio.to_thread(
        verify_bearer_token,
        authorization.split(" ", 1)[1],
        settings.supabase_jwt_secret,
        project_url=settings.supabase_project_url,
    )

    if settings.allowed_emails:
        allowed = {e.lower() for e in settings.allowed_emails}
        if (auth.email or "").lower() not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorized for this instance")

    return await _get_or_create_user(session, auth)


@lru_cache
def get_object_store() -> ObjectStore:
    """Return the configured object store (DESIGN.md §11.1).

    ``memory`` is for tests; production uses Supabase Storage. Cached so the
    HTTP-client-backed store is reused across requests.
    """
    settings = get_settings()
    if settings.storage_backend == "memory":
        from ..storage.memory import MemoryObjectStore

        return MemoryObjectStore()
    if settings.storage_backend == "local":
        from ..storage.local import LocalFileObjectStore

        return LocalFileObjectStore(settings.local_storage_dir)
    from ..storage.supabase import SupabaseObjectStore

    return SupabaseObjectStore(
        settings.supabase_project_url, settings.supabase_service_key, settings.storage_bucket
    )


DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]
Storage = Annotated[ObjectStore, Depends(get_object_store)]
