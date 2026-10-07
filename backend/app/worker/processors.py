"""Job processors (DESIGN.md §10, §11.2, §12).

A processor advances a job through stages via ``ctx.heartbeat`` (which persists
progress, renews the lease, and raises on cancellation) and publishes its result.

This slice adds the restricted-egress fetch stage. The real extraction pipeline
(grant col:line, native + OCR, artifact publication) replaces the placeholder
"mark ready" tail in Phase 3.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace

from ..config import get_settings
from ..db.models import ExtractionJob, SourceDocument, UserDocument
from ..enrichment.google_text import ProviderText, extract_provider_text
from ..extraction.artifact import Artifact, PatentFrontMatter
from ..extraction.config import ExtractionConfig
from ..fetch.adapter import RestrictedFetcher
from ..fetch.errors import FetchError
from ..fetch.google_patents import extract_pdf_url, patent_page_url
from ..fetch.guard import check_url
from ..fetch.ppubs import PpubsClient
from ..patents import PatentParseError, parse_patent_identifier
from ..services.publication import publish_artifact
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

    Resolves and downloads the official PPUBS PDF first, with Google Patents as a
    compatibility fallback. Every request is SSRF-guarded and size/time capped.
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

    provider_title: str | None = None
    try:
        identity = parse_patent_identifier(canonical)
        ppubs = PpubsClient(fetcher, max_json_bytes=settings.fetch_max_html_bytes)
        document = await ppubs.resolve(identity)
        pdf = await ppubs.fetch_pdf(document, max_bytes=settings.fetch_max_pdf_bytes)
        provider_title = document.title
    except (FetchError, PatentParseError):
        page = await fetcher.fetch(
            patent_page_url(canonical), max_bytes=settings.fetch_max_html_bytes
        )
        page_html = page.content.decode("utf-8", "replace")
        provider = extract_provider_text(page_html)
        pdf_url = extract_pdf_url(page_html)
        check_url(pdf_url, allowed)  # the PDF URL must also be on the allowlist
        pdf = await fetcher.fetch(pdf_url, max_bytes=settings.fetch_max_pdf_bytes, expect_pdf=True)
        provider_title = provider.title

    key = f"sources/{owner_id}/{source_id}.pdf"
    await ctx.store.write(key, pdf.content, content_type="application/pdf")

    async with ctx.sessionmaker() as session:
        src = await session.get(SourceDocument, source_id)
        doc = await session.get(UserDocument, job.document_id) if job.document_id else None
        src.pdf_object_key = key
        src.sha256 = hashlib.sha256(pdf.content).hexdigest()
        src.byte_size = len(pdf.content)
        src.state = "uploaded"
        if doc is not None and provider_title:
            try:
                default_title = parse_patent_identifier(canonical).display
            except PatentParseError:
                default_title = canonical
            if doc.title == default_title:
                doc.title = provider_title.strip()
        await session.commit()


async def extraction_processor(
    ctx: JobContext, job: ExtractionJob, *, fetcher: RestrictedFetcher | None = None
) -> None:
    """Full pipeline: fetch the source if needed, run the native extraction core,
    align it to the provider's clean text, and atomically publish the immutable
    artifact (DESIGN.md §9.2, §12, §13)."""
    if await _needs_fetch(ctx, job):
        await fetch_source(ctx, job, fetcher=fetcher)

    async with ctx.sessionmaker() as session:
        doc = await session.get(UserDocument, job.document_id) if job.document_id else None
        source = await session.get(SourceDocument, doc.source_id) if doc else None
        pdf_key = source.pdf_object_key if source else None
        source_id = source.id if source else None
        owner_id = source.owner_id if source else None
        doc_type = (source.doc_type if source else None) or "auto"
        canonical = source.patent_canonical if source else None
        title = doc.title if doc else None
    if not pdf_key or source_id is None or job.document_id is None:
        raise FetchError("no_source_pdf", "job has no source PDF to extract")

    settings = get_settings()
    pdf_bytes = await ctx.store.read(pdf_key, limit=settings.max_upload_bytes)

    from ..extraction.config import DEFAULT_CONFIG
    from .extraction import run_extraction

    # Resume-aware: reuse valid page_text checkpoints, recompute the rest, then
    # recompute the fast downstream pipeline fresh (§10.6–10.7).
    artifact = await run_extraction(
        ctx,
        pdf_bytes=pdf_bytes,
        source_id=source_id,
        owner_id=owner_id,
        config=DEFAULT_CONFIG,
        doc_type=doc_type,
    )

    artifact = await _enrich_artifact(
        ctx, artifact, config=DEFAULT_CONFIG, canonical=canonical, title=title, fetcher=fetcher
    )

    await ctx.heartbeat(stage="persisting_artifact", stage_label="Publishing")
    async with ctx.sessionmaker() as session:
        await publish_artifact(
            session,
            ctx.store,
            document_id=job.document_id,
            source_id=source_id,
            owner_id=owner_id,
            artifact=artifact,
        )


async def _enrich_artifact(
    ctx: JobContext,
    artifact: Artifact,
    *,
    config: ExtractionConfig,
    canonical: str | None,
    title: str | None,
    fetcher: RestrictedFetcher | None = None,
) -> Artifact:
    """Clean-text alignment stage (DESIGN.md §13): fetch the provider page, verify
    identity, and align each line's ``display_text`` to the authoritative text —
    ``source_text`` is never touched. Best-effort: a disabled instance, a missing
    patent identity, a fetch failure, or an identity mismatch all fall back to the
    unaligned artifact, so extraction still publishes."""
    settings = get_settings()
    base_front_matter = (
        PatentFrontMatter(title=title, patent_number=canonical)
        if title or canonical
        else None
    )
    if base_front_matter is not None:
        artifact = replace(artifact, front_matter=base_front_matter)
    if not settings.enrichment_enabled or not canonical:
        return artifact

    from ..enrichment import (
        enrich_from_page_html,
        enrich_from_ppubs_html,
        extract_ppubs_text,
    )
    from ..enrichment.identity import verify_identity

    await ctx.heartbeat(
        stage="aligning_text", stage_label="Aligning clean text", indeterminate=True
    )
    fetcher = fetcher or RestrictedFetcher(
        settings.fetch_allowed_hosts, max_redirects=settings.fetch_max_redirects
    )
    provider_name = "uspto_ppubs"
    selected_provider: ProviderText | None = None
    try:
        patent_identity = parse_patent_identifier(canonical)
        ppubs = PpubsClient(fetcher, max_json_bytes=settings.fetch_max_html_bytes)
        document = await ppubs.resolve(patent_identity)
        page = await ppubs.fetch_text(document, max_bytes=settings.fetch_max_html_bytes)
        html = page.content.decode("utf-8", "replace")
        ppubs_provider = extract_ppubs_text(html)
        # Alignment over the whole specification is CPU-bound — keep it off the loop.
        aligned, identity = await asyncio.to_thread(
            enrich_from_ppubs_html,
            artifact.entries,
            html,
            canonical,
            config,
            source_title=title,
        )
        aligned_count = _aligned_count(aligned)
        usable = (
            bool(ppubs_provider.clean_text)
            and identity.status == "verified"
            and (not artifact.entries or aligned_count > 0)
        )
        if not usable:
            raise FetchError("ppubs_text_unusable", "PPUBS did not provide usable patent text")
        selected_provider = ppubs_provider
    except (FetchError, PatentParseError):
        provider_name = "google_patents"
        try:
            page = await fetcher.fetch(
                patent_page_url(canonical), max_bytes=settings.fetch_max_html_bytes
            )
        except FetchError:
            return artifact  # graceful: keep the unaligned entries
        html = page.content.decode("utf-8", "replace")
        google_provider = extract_provider_text(html)
        aligned, identity = await asyncio.to_thread(
            enrich_from_page_html,
            artifact.entries,
            html,
            canonical,
            config,
            source_title=title,
        )
        if identity.status == "verified":
            selected_provider = google_provider
        aligned_count = _aligned_count(aligned)

    # PPUBS is authoritative for specification wording, but its text endpoint
    # does not consistently carry the abstract/front-page metadata. Fill that
    # display-only preface from a verified Google page when available without
    # replacing the PPUBS-aligned specification.
    front_source = provider_name
    if provider_name == "uspto_ppubs" and selected_provider is not None:
        try:
            google_page = await fetcher.fetch(
                patent_page_url(canonical), max_bytes=settings.fetch_max_html_bytes
            )
            google_provider = extract_provider_text(
                google_page.content.decode("utf-8", "replace")
            )
            google_identity = verify_identity(
                canonical,
                google_provider.canonical,
                source_title=title,
                provider_title=google_provider.title,
            )
            if google_identity.status == "verified" and (
                google_provider.abstract or google_provider.metadata
            ):
                selected_provider = google_provider
                front_source = "google_patents"
        except FetchError:
            pass

    front_matter = _front_matter(
        selected_provider,
        fallback_title=title,
        fallback_number=canonical,
        source=front_source if selected_provider is not None else None,
    )
    quality = {
        **artifact.quality,
        "identity_status": identity.status,
        "aligned_lines": aligned_count,
        "text_provider": provider_name,
    }
    return replace(
        artifact,
        entries=aligned,
        front_matter=front_matter,
        quality=quality,
    )


def _aligned_count(entries) -> int:
    return sum(
        1 for entry in entries if entry.provenance.alignment_method in ("exact", "fuzzy")
    )


def _front_matter(
    provider: ProviderText | None,
    *,
    fallback_title: str | None,
    fallback_number: str | None,
    source: str | None,
) -> PatentFrontMatter:
    """Build a bounded, display-only patent preface from verified provider data."""
    grouped: dict[str, list[str]] = {}
    for label, value in provider.metadata if provider is not None else ():
        clean_label = label.strip()[:80]
        clean_value = value.strip()[:500]
        if clean_label and clean_value and clean_value not in grouped.setdefault(clean_label, []):
            grouped[clean_label].append(clean_value)
    metadata = [
        {"label": label, "value": "; ".join(values)}
        for label, values in grouped.items()
    ]
    abstract = provider.abstract.strip()[:20_000] if provider and provider.abstract else None
    return PatentFrontMatter(
        title=(provider.title if provider and provider.title else fallback_title),
        patent_number=(
            provider.canonical if provider and provider.canonical else fallback_number
        ),
        abstract=abstract,
        metadata=metadata,
        source=source,
    )


async def dispatch_processor(ctx: JobContext, job: ExtractionJob) -> None:
    """Route a job: fetch the source first if it still needs downloading, then
    (placeholder) mark the document ready. Retained for the stub demo path."""
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
