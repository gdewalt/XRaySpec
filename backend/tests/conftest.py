"""Test fixtures: an ASGI client backed by in-memory SQLite + a JWT minter.

Env is set before any app import so ``get_settings`` picks up the test JWT secret
and an empty allowlist (empty = allow all). The DB dependency is overridden with
an in-memory SQLite session, so tests need neither asyncpg nor a live database.
"""

from __future__ import annotations

import os

os.environ.setdefault("XRAY_SUPABASE_JWT_SECRET", "test-secret-0123456789-abcdefghij-XYZ")
os.environ.setdefault("XRAY_ALLOWED_EMAILS", "[]")

from datetime import UTC, datetime, timedelta  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

JWT_SECRET = "test-secret-0123456789-abcdefghij-XYZ"


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    import app.db  # noqa: F401  register models on Base.metadata
    from app.api.deps import get_db, get_object_store
    from app.db.base import Base
    from app.main import app
    from app.storage.memory import MemoryObjectStore

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    test_sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with test_sessionmaker() as session:
            yield session

    store = MemoryObjectStore()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_object_store] = lambda: store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.object_store = store  # tests reach the store to simulate the browser PUT
        c.sessionmaker = test_sessionmaker  # tests set up / inspect rows directly
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
def make_token():
    def _make(sub: str, email: str | None = None) -> str:
        payload = {
            "sub": sub,
            "aud": "authenticated",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        if email is not None:
            payload["email"] = email
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    return _make


@pytest.fixture
def auth():
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return _auth
