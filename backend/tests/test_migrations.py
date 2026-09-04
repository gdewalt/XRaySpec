"""Alembic baseline migration (DESIGN.md §18.4, §19.3).

Guards two things in CI: the baseline actually applies, and it stays in sync with
the ORM models — so a model change shipped without a matching migration fails
here instead of at deploy. Runs synchronously (no ``async def``) so Alembic's env,
which drives an async engine via ``asyncio.run``, has no running loop to collide
with. Uses a throwaway SQLite file; migrations target Postgres in production but
the schema is built from portable generic types.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

import app.db  # noqa: F401  register every model on Base.metadata
from app.config import get_settings
from app.db.base import Base

BACKEND = Path(__file__).resolve().parents[1]


def _config(db_path: Path) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


def test_baseline_applies_and_creates_every_table(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("XRAY_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    get_settings.cache_clear()
    try:
        command.upgrade(_config(db), "head")

        engine = sa.create_engine(f"sqlite:///{db}")
        tables = set(sa.inspect(engine).get_table_names())
        engine.dispose()
    finally:
        get_settings.cache_clear()

    expected = set(Base.metadata.tables) | {"alembic_version"}
    assert expected <= tables, expected - tables


def test_baseline_has_no_drift_from_models(tmp_path, monkeypatch):
    """Applying the migrations then diffing against the models yields no changes —
    the equivalent of ``alembic check``."""
    db = tmp_path / "m.db"
    monkeypatch.setenv("XRAY_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    get_settings.cache_clear()
    try:
        command.upgrade(_config(db), "head")

        engine = sa.create_engine(f"sqlite:///{db}")
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diff = compare_metadata(ctx, Base.metadata)
        engine.dispose()
    finally:
        get_settings.cache_clear()

    assert diff == [], f"models drifted from migrations: {diff}"
