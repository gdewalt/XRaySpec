"""Job processors (DESIGN.md §10, §11.2, §12).

A processor advances a job through stages via ``ctx.heartbeat`` (which persists
progress, renews the lease, and raises on cancellation) and publishes its result.

This slice adds the restricted-egress fetch stage. The real extraction pipeline
(grant col:line, native + OCR, artifact publication) replaces the placeholder
"mark ready" tail in Phase 3.
"""

from __future__ import annotations

import hashlib

from ..config import get_settings
from ..db.models import ExtractionJob, SourceDocument, UserDocument
from ..fetch.adapter import RestrictedFetcher
from ..fetch.errors import FetchError
from ..fetch.google_patents import extract_pdf_url, patent_page_url
from ..fetch.guard import check_url
from .engine import JobContext


async def stub_processor(ctx: JobContext, job: ExtractionJob) -> None:
    """Placeholder processor: walk a few steps, then mark the document ready."""
    total = 4
    await ctx.heartbeat(
        stage="validating_source",
        stage_label="Preparing",
        total_units=total,
        unit="steps",
        completed_units=0,
        indeterminate=False,
    )
    for step in range(1, total + 1):
        await ctx.heartbeat(stage="processing", completed_units=step)
    await _mark_ready(ctx, job)


async def fetch_source(
    ctx: JobContext, job: ExtractionJob, *, fetcher: RestrictedFetcher | None = None
) -> None:
    """Download the source PDF for a fetch job through the restricted adapter.

    Resolves the Google Patents page, extracts the citation PDF URL, downloads it
    (SSRF-guarded, byte/time capped), verifies it is a PDF, stores it privately,
    and records the observed hash/size on the source. The web tier never does this.
    """
    settings = get_settings()
    fetcher = fetcher or RestrictedFetcher(
        settings.fetch_allowed_hosts, max_redirects=settings.fetch_max_redirects
    )
    allowed = {h.lower() for h in settings.fetch_allowed_hosts}

    await ctx.heartbeat(stage="fetching_source", stage_label="Fetching patent", indeterminate=True)

    async with ctx.sessionmaker() as session:
        doc = await session.get(UserDocument, job.document_id) if job.document_id else None
        source = await session.get(SourceDocument, doc.source_id) if doc else None
        canonical = source.patent_canonical if source else None
        source_id = source.id if source else None
        owner_id = source.owner_id if source else None
    if not canonical or source_id is None:
        raise FetchError("no_source", "fetch job has no patent identity")

    page = await fetcher.fetch(patent_page_url(canonical), max_bytes=settings.fetch_max_html_bytes)
    pdf_url = extract_pdf_url(page.content.decode("utf-8", "replace"))
    check_url(pdf_url, allowed)  # the PDF URL must also be on the allowlist
    pdf = await fetcher.fetch(pdf_url, max_bytes=settings.fetch_max_pdf_bytes, expect_pdf=True)

    key = f"sources/{owner_id}/{source_id}.pdf"
    await ctx.store.write(key, pdf.content, content_type="application/pdf")

    async with ctx.sessionmaker() as session:
        src = await session.get(SourceDocument, source_id)
        src.pdf_object_key = key
        src.sha256 = hashlib.sha256(pdf.content).hexdigest()
        src.byte_size = len(pdf.content)
        src.state = "uploaded"
        await session.commit()


async def dispatch_processor(ctx: JobContext, job: ExtractionJob) -> None:
    """Route a job: fetch the source first if it still needs downloading, then
    (placeholder) mark the document ready. Real extraction slots in here."""
    if await _needs_fetch(ctx, job):
        await fetch_source(ctx, job)
    await _mark_ready(ctx, job)


async def _needs_fetch(ctx: JobContext, job: ExtractionJob) -> bool:
    if not job.document_id:
        return False
    async with ctx.sessionmaker() as session:
        doc = await session.get(UserDocument, job.document_id)
        source = await session.get(SourceDocument, doc.source_id) if doc else None
        return bool(source and source.source_type == "fetch" and source.pdf_object_key is None)


async def _mark_ready(ctx: JobContext, job: ExtractionJob) -> None:
    if job.document_id is None:
        return
    async with ctx.sessionmaker() as session:
        doc = await session.get(UserDocument, job.document_id)
        if doc is not None and doc.state != "deleted":
            doc.state = "ready"
            await session.commit()
