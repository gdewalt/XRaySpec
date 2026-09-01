"""Durable storage layer (SQLAlchemy 2.0 async). See DESIGN.md §7, §9.

Importing this package also imports ``models`` so every table is registered on
``Base.metadata`` (needed by ``create_all`` in tests and Alembic autogenerate).
"""

from . import models  # noqa: F401  (register tables on Base.metadata)
from .base import Base, get_engine, get_sessionmaker

__all__ = ["Base", "get_engine", "get_sessionmaker", "models"]
