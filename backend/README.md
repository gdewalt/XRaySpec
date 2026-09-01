# X-Ray Spec — backend

FastAPI service + clean-room extraction engine + workers. See the root
[DESIGN.md](../DESIGN.md) for the authoritative design.

## Quickstart

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env            # fill in Supabase values before hitting a DB
uvicorn app.main:app --reload   # http://localhost:8000/docs
pytest
ruff check .
```

## Layout

| Path | Role | DESIGN.md |
|---|---|---|
| `app/main.py` | FastAPI entrypoint, `/live` `/ready` | §5, §18.1 |
| `app/config.py` | instance settings (env) | §20 |
| `app/api/` | versioned HTTP routers | §14 |
| `app/auth/` | Supabase JWT verification | §17.1 |
| `app/db/` | SQLAlchemy models (storage shape) | §7 |
| `app/schemas/` | Pydantic wire types (API shape) | §14 |
| `app/extraction/` | **clean-room extraction core** | §8, §12, §25.1 |
| `app/worker/` | Postgres queue claim + worker loop | §10 |
| `tests/` | pytest; goldens from labeled corpus | §19, §25.3 |

## The extraction seam

`app/extraction/core.py` exposes the one contract everything hangs off:

```python
def extract(pdf_bytes: bytes, config: ExtractionConfig) -> Artifact: ...
```

Pure — no web, DB, storage, or global state — so it runs inside the sandboxed
worker and is golden-testable in isolation. Deterministic and geometry-anchored:
models may propose, geometry confirms, source text is never model-overwritten
(§25.1). Implementation lands in Phase 2 (grants) and Phase 3 (hybrid,
applications, figures, callouts).

## Migrations

Alembic is configured (`alembic.ini` + async `migrations/env.py` targeting
`Base.metadata`). Autogenerate the first revision against your database:

```bash
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

## Tests

```bash
pytest            # auth gate + per-user IDOR matrix
```

Tests use in-memory SQLite (no live DB / asyncpg needed) and mint their own
HS256 JWTs, so they run offline. Requires Python 3.11+ with `pip install -e
".[dev]"` (the default interpreter may be newer than some wheels support — use a
3.11/3.12 venv if `pip install` fails to find wheels).
