"""SQLAlchemy 2.0 async engine + session + declarative base (DESIGN.md §25.2).

Engine/session construction is lazy (``get_engine`` / ``get_sessionmaker``) so
importing the app has no side effects and the async driver (asyncpg) is only
required when a real connection is made — not at import time, and not in tests
(which override ``get_db`` with an in-memory SQLite session).

ORM models (the *storage* shape) are kept separate from Pydantic wire schemas
(the *API* shape). Drop to core SQL for the queue claim and owner-predicate
composition where the ORM is awkward.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models (DESIGN.md §7)."""


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
