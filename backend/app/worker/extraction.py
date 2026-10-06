"""Checkpoint-aware extraction with resume (DESIGN.md §10.6–10.7).

Wraps the pure extraction core with the resume contract: preflight the source
(``source_ready``), then for each page reuse a valid ``page_text`` checkpoint or
compute it (native/OCR) and persist it, then **recompute the entire fast
downstream pipeline fresh**. Recomputing downstream every time is cheaper than
persisting/version-verifying it and guarantees the result matches the current
engine/config.

The PDF/OCR entry points are injected (defaulting to the real core functions) so
the resume logic is unit-testable without the PDF/OCR libraries.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable

from ..extraction.artifact import Artifact
from ..extraction.config import ExtractionConfig
from ..extraction.core import PageResult, assemble_from_results
from ..extraction.model import Page
from .checkpoints import (
    load_page_texts,
    load_source_ready,
    resume_cache_key,
    save_page_text,
    save_source_ready,
)
from .engine import JobContext


async def _route_page_with_lease(
    ctx: JobContext,
    route_page: Callable[..., PageResult],
    pdf_bytes: bytes,
    native_page: Page,
    config: ExtractionConfig,
    can_ocr: bool,
) -> PageResult:
    """Run CPU-bound page OCR while renewing the database lease."""
    task = asyncio.create_task(
        asyncio.to_thread(route_page, pdf_bytes, native_page, config, can_ocr)
    )
    interval = max(5.0, min(20.0, ctx.lease_seconds / 3))
    while True:
        done, _ = await asyncio.wait({task}, timeout=interval)
        if task in done:
            return await task
        await ctx.heartbeat()


async def run_extraction(
    ctx: JobContext,
    *,
    pdf_bytes: bytes,
    source_id: str,
    owner_id: str,
    config: ExtractionConfig,
    doc_type: str,
    load_pages: Callable[[bytes], list[Page]] | None = None,
    route_page: Callable[..., PageResult] | None = None,
    ocr_available: Callable[[], bool] | None = None,
) -> Artifact:
    """Extract an artifact, reusing valid checkpoints and persisting new ones.

    On a fresh run every page is computed and checkpointed. On a resume (retry or
    a superseding attempt with unchanged source/engine/config) already-computed
    pages are reused, skipping their OCR; only missing pages are recomputed.
    """
    source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    cache_key = resume_cache_key(source_sha256, config)

    async with ctx.sessionmaker() as session:
        cached = await load_page_texts(session, source_id, cache_key)
        source_ready = await load_source_ready(session, source_id, cache_key)

    total_pages = source_ready["total_pages"] if source_ready else None
    complete_resume = (
        total_pages is not None
        and len(cached) == total_pages
        and all(i in cached for i in range(total_pages))
    )

    if complete_resume:
        # Every page is checkpointed — skip the PDF parse entirely (§10.7).
        results = [cached[i] for i in range(total_pages)]
        await ctx.heartbeat(
            stage="extracting_text",
            stage_label="Resuming from checkpoints",
            total_units=total_pages,
            completed_units=total_pages,
            unit="pages",
            indeterminate=False,
        )
    else:
        if load_pages is None:
            from ..extraction.pdf import load_pages
        if route_page is None:
            from ..extraction.core import route_page
        if ocr_available is None:
            from ..extraction.ocr import ocr_available

        native_pages = await asyncio.to_thread(load_pages, pdf_bytes)
        total_pages = len(native_pages)
        async with ctx.sessionmaker() as session:
            await save_source_ready(
                session, owner_id=owner_id, source_id=source_id,
                cache_key=cache_key, total_pages=total_pages,
            )

        can_ocr = ocr_available()
        await ctx.heartbeat(
            stage="extracting_text",
            stage_label="Reading pages",
            total_units=total_pages,
            completed_units=0,
            unit="pages",
            indeterminate=False,
        )
        results = []
        for done, native_page in enumerate(native_pages, start=1):
            if native_page.index in cached:
                results.append(cached[native_page.index])
            else:
                result = await _route_page_with_lease(
                    ctx, route_page, pdf_bytes, native_page, config, can_ocr
                )
                async with ctx.sessionmaker() as session:
                    await save_page_text(
                        session, owner_id=owner_id, source_id=source_id,
                        cache_key=cache_key, result=result,
                    )
                results.append(result)
            # Cancellation is honored between pages (§10.5) and progress is durable.
            await ctx.heartbeat(completed_units=done)

    return await asyncio.to_thread(
        assemble_from_results,
        results, config, source_sha256=source_sha256, doc_type=doc_type, total_pages=total_pages,
    )
