"""Dev-only: open a corpus PDF in the viewer to review its labels (DESIGN.md §19).

The app normally ingests via upload + the async worker against Supabase. For
reviewing the labeled corpus locally that is overkill, so this seeds one patent
directly into a throwaway SQLite DB + local file store (extract -> store PDF ->
publish artifact) and prints a document id, a dev token, and the URL to open.

Usage:
    python -m scripts.review_corpus US6411897        # seed one doc
    python -m scripts.review_corpus --list           # list corpus PDFs

Then, in two more terminals (same directory), start the API and the SPA with the
env this script prints, sign in by pasting the token, and click Open. All state
lives in backend/devreview.db + backend/devreview_store/ (gitignored; delete to
reset).
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DB_PATH = BACKEND / "devreview.db"
STORE_DIR = BACKEND / "devreview_store"
SECRET = "dev-review-secret-0123456789-abcdef"
EMAIL = "dev@example.com"

os.environ.setdefault("XRAY_DATABASE_URL", f"sqlite+aiosqlite:///{DB_PATH}")
os.environ.setdefault("XRAY_STORAGE_BACKEND", "local")
os.environ.setdefault("XRAY_LOCAL_STORAGE_DIR", str(STORE_DIR))
os.environ.setdefault("XRAY_SUPABASE_JWT_SECRET", SECRET)
os.environ.setdefault("XRAY_ALLOWED_EMAILS", "[]")

PDF_DIR = BACKEND / "corpus" / "pdfs"


def _list() -> None:
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print(f"{len(pdfs)} corpus PDFs in {PDF_DIR}:")
    for p in pdfs:
        print(f"  {p.stem}")


def _token() -> str:
    import jwt

    return jwt.encode(
        {
            "sub": "dev-reviewer",
            "email": EMAIL,
            "aud": "authenticated",
            "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=12),
        },
        SECRET,
        algorithm="HS256",
    )


async def _seed(doc_id: str) -> str:
    from app.db.base import Base, get_engine, get_sessionmaker
    from app.db.models import SourceDocument, UserDocument
    from app.extraction.config import DEFAULT_CONFIG
    from app.extraction.core import extract
    from app.services.publication import publish_artifact
    from app.storage.local import LocalFileObjectStore

    pdf_path = PDF_DIR / f"{doc_id}.pdf"
    if not pdf_path.exists():
        raise SystemExit(f"No such corpus PDF: {pdf_path} (try --list)")

    pdf = pdf_path.read_bytes()
    doc_type = "application" if doc_id.upper().endswith(("A1", "A2")) else "grant"
    print(f"Extracting {doc_id} ({doc_type})… (scanned patents take a few minutes)")
    artifact = await asyncio.to_thread(extract, pdf, DEFAULT_CONFIG, doc_type)
    print(f"  {len(artifact.entries)} lines, {len(artifact.callout_occurrences)} callouts")

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    store = LocalFileObjectStore(str(STORE_DIR))

    async with get_sessionmaker()() as s:
        user = await _get_or_create_user(s)
        pdf_key = f"sources/{user.id}/{doc_id}.pdf"
        await store.write(pdf_key, pdf, content_type="application/pdf")
        src = SourceDocument(
            owner_id=user.id, source_type="upload", doc_type=doc_type,
            patent_canonical=doc_id, pdf_object_key=pdf_key,
            sha256=hashlib.sha256(pdf).hexdigest(), byte_size=len(pdf), state="ready",
        )
        s.add(src)
        await s.flush()
        doc = UserDocument(owner_id=user.id, source_id=src.id, title=doc_id, state="processing")
        s.add(doc)
        await s.commit()
        ids = (doc.id, src.id, user.id)

    async with get_sessionmaker()() as s:
        await publish_artifact(
            s, store, document_id=ids[0], source_id=ids[1], owner_id=ids[2], artifact=artifact
        )
    await get_engine().dispose()
    return ids[0]


async def _get_or_create_user(session):
    from sqlalchemy import select

    from app.db.models import User

    user = await session.scalar(select(User).where(User.subject == "dev-reviewer"))
    if user is None:
        user = User(subject="dev-reviewer", email=EMAIL)
        session.add(user)
        await session.flush()
    return user


def _serve() -> None:
    """Run the API with the review env already applied in-process (this module set
    it at import), so no shell-specific env-var command is needed."""
    import uvicorn

    print(f"API on http://localhost:8000  (DB {DB_PATH.name}, local store)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")


def main() -> None:
    if "--list" in sys.argv:
        _list()
        return
    if "--serve" in sys.argv:
        _serve()
        return

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        raise SystemExit(
            "Usage:\n"
            "  python -m scripts.review_corpus <DOC_ID>   # seed a document\n"
            "  python -m scripts.review_corpus --serve    # run the API\n"
            "  python -m scripts.review_corpus --list     # list corpus PDFs"
        )

    doc_id = asyncio.run(_seed(args[0]))
    token = _token()
    (BACKEND / "devreview_token.txt").write_text(token + "\n", encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"Seeded document {doc_id}")
    print("Now run these two commands in two more terminals:")
    print()
    print("  # terminal 2 — API (from backend/) — sets the right env itself")
    print("  python -m scripts.review_corpus --serve")
    print()
    print("  # terminal 3 — SPA (from frontend/)")
    print("  npm run dev")
    print()
    print("Open http://localhost:5173 and paste this token on the sign-in screen")
    print("(also saved to backend/devreview_token.txt):")
    print()
    print(f"  {token}")
    print("=" * 70)


if __name__ == "__main__":
    main()
