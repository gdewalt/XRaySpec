"""Fetch processor: download → store → update source (DESIGN.md §11.2)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models import ExtractionJob, SourceDocument, User, UserDocument
from app.fetch.adapter import FetchResult
from app.worker.engine import JobContext
from app.worker.processors import fetch_source

PDF_URL = "https://patentimages.storage.googleapis.com/x.pdf"
HTML = (
    f'<meta name="citation_pdf_url" content="{PDF_URL}">'
    '<meta name="DC.title" content="Adaptive imaging system">'
).encode()
PDF = b"%PDF-1.7 body bytes"


class FakeFetcher:
    def __init__(self, html: bytes, pdf: bytes) -> None:
        self.html = html
        self.pdf = pdf
        self.calls: list[str] = []

    async def fetch(self, url: str, *, max_bytes: int, expect_pdf: bool = False) -> FetchResult:
        self.calls.append(url)
        if expect_pdf:
            return FetchResult(self.pdf, "application/pdf", url)
        return FetchResult(self.html, "text/html", url)


async def _seed_fetch_job(client, canonical="US12262260B2") -> tuple[str, str]:
    async with client.sessionmaker() as s:
        user = User(subject=uuid.uuid4().hex, email="o@example.com")
        s.add(user)
        await s.flush()
        source = SourceDocument(
            owner_id=user.id,
            source_type="fetch",
            doc_type="grant",
            patent_canonical=canonical,
            state="pending_fetch",
        )
        s.add(source)
        await s.flush()
        doc = UserDocument(owner_id=user.id, source_id=source.id, title="t", state="processing")
        s.add(doc)
        await s.flush()
        job = ExtractionJob(
            owner_id=user.id, document_id=doc.id, status="running", fencing_token="tok"
        )
        s.add(job)
        await s.commit()
        return job.id, source.id


async def test_fetch_source_downloads_and_records(client):
    job_id, source_id = await _seed_fetch_job(client)
    async with client.sessionmaker() as s:
        job = await s.get(ExtractionJob, job_id)

    ctx = JobContext(
        client.sessionmaker,
        client.object_store,
        job_id=job_id,
        fencing_token="tok",
        lease_seconds=30,
    )
    fetcher = FakeFetcher(HTML, PDF)
    await fetch_source(ctx, job, fetcher=fetcher)

    async with client.sessionmaker() as s:
        source = await s.get(SourceDocument, source_id)
    assert source.pdf_object_key is not None
    assert source.byte_size == len(PDF)
    assert source.sha256 and source.state == "uploaded"
    assert len(client.object_store._objects) == 1  # PDF stored privately
    # Resolved the page first, then the PDF.
    assert fetcher.calls[0].endswith("/en")
    assert fetcher.calls[1] == PDF_URL


async def test_fetch_source_replaces_default_number_with_patent_title(client):
    job_id, source_id = await _seed_fetch_job(client)
    async with client.sessionmaker() as session:
        job = await session.get(ExtractionJob, job_id)
        document = await session.scalar(
            select(UserDocument).where(UserDocument.source_id == source_id)
        )
        assert document is not None
        document.title = "US 12,262,260 B2"
        await session.commit()

    ctx = JobContext(
        client.sessionmaker,
        client.object_store,
        job_id=job_id,
        fencing_token="tok",
        lease_seconds=30,
    )
    await fetch_source(ctx, job, fetcher=FakeFetcher(HTML, PDF))

    async with client.sessionmaker() as session:
        document = await session.scalar(
            select(UserDocument).where(UserDocument.source_id == source_id)
        )
    assert document is not None
    assert document.title == "Adaptive imaging system"
