"""SQLAlchemy 2.0 async engine + session + declarative base (DESIGN.md §25.2).

ORM models (the *storage* shape) are kept separate from Pydantic wire schemas
(the *API* shape). Drop to core SQL for the queue claim and owner-predicate
composition where the ORM is awkward.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models (DESIGN.md §7)."""


_settings = get_settings()
engine = create_async_engine(_settings.database_url, pool_pre_ping=True, future=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
