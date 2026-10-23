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
import logging
import time
from collections.abc import Awaitable, Callable

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

_CHECKPOINT_BATCH_SIZE = 4
_PAGE_WORKERS = 2

logger = logging.getLogger(__name__)


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
    on_text_ready: Callable[[Artifact], Awaitable[None]] | None = None,
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
        default_load_pages = load_pages is None
        if default_load_pages:
            from ..extraction.pdf import load_pages
        default_route_page = route_page is None
        if default_route_page:
            from ..extraction.core import complete_drawing_page, route_page
        if ocr_available is None:
            from ..extraction.ocr import ocr_available

        if default_load_pages:
            native_pages = await asyncio.to_thread(
                load_pages,
                pdf_bytes,
                detect_bold_numbers=config.callout_detection_enabled,
            )
        else:
            native_pages = await asyncio.to_thread(load_pages, pdf_bytes)
        total_pages = len(native_pages)
        async with ctx.sessionmaker() as session:
            await save_source_ready(
                session,
                owner_id=owner_id,
                source_id=source_id,
                cache_key=cache_key,
                total_pages=total_pages,
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
        results_by_index = {
            page.index: cached[page.index] for page in native_pages if page.index in cached
        }
        pending_checkpoints: list[PageResult] = []
        last_checkpoint_commit = time.monotonic()
        checkpoint_interval = max(5.0, min(20.0, ctx.lease_seconds / 3))
        completed = len(results_by_index)
        missing = [page for page in native_pages if page.index not in results_by_index]
        worker_slots = asyncio.Semaphore(_PAGE_WORKERS)
        stage_drawings = default_route_page and on_text_ready is not None
        deferred_drawing_indices: set[int] = set()

        async def compute(native_page: Page) -> PageResult:
            async with worker_slots:
                if stage_drawings:
                    return await asyncio.to_thread(
                        route_page,
                        pdf_bytes,
                        native_page,
                        config,
                        can_ocr,
                        defer_drawing_details=True,
                    )
                return await asyncio.to_thread(
                    route_page, pdf_bytes, native_page, config, can_ocr
                )

        tasks = {asyncio.create_task(compute(page)) for page in missing}
        all_tasks = set(tasks)
        try:
            while tasks:
                finished, tasks = await asyncio.wait(
                    tasks,
                    timeout=checkpoint_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not finished:
                    await ctx.heartbeat(completed_units=completed)
                    continue

                for task in finished:
                    result = await task
                    results_by_index[result.page_index] = result
                    if stage_drawings and result.is_drawing:
                        deferred_drawing_indices.add(result.page_index)
                    else:
                        pending_checkpoints.append(result)
                    completed += 1

                # Commit checkpoints and their matching progress update together.
                # Small batches retain durable resume points while avoiding two
                # Supabase transactions for every page.
                commit_due = (
                    len(pending_checkpoints) >= _CHECKPOINT_BATCH_SIZE
                    or not tasks
                    or time.monotonic() - last_checkpoint_commit >= checkpoint_interval
                )
                if commit_due and pending_checkpoints:
                    async with ctx.sessionmaker() as session:
                        for checkpoint in pending_checkpoints:
                            await save_page_text(
                                session,
                                owner_id=owner_id,
                                source_id=source_id,
                                cache_key=cache_key,
                                result=checkpoint,
                                commit=False,
                            )
                        await ctx.heartbeat_in_session(session, completed_units=completed)
                        await session.commit()
                    logger.info(
                        "extraction progress job_id=%s pages=%s/%s",
                        ctx.job_id,
                        completed,
                        total_pages,
                    )
                    pending_checkpoints.clear()
                    last_checkpoint_commit = time.monotonic()
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)
            raise

        if deferred_drawing_indices:
            preliminary_results = [results_by_index[page.index] for page in native_pages]
            preliminary = await asyncio.to_thread(
                assemble_from_results,
                preliminary_results,
                config,
                source_sha256=source_sha256,
                doc_type=doc_type,
                total_pages=total_pages,
            )
            await ctx.heartbeat(
                stage="publishing_text",
                stage_label="Opening text and PDF",
                indeterminate=True,
            )
            assert on_text_ready is not None
            await on_text_ready(preliminary)

            drawing_pages = {
                page.index: page
                for page in native_pages
                if page.index in deferred_drawing_indices
            }
            await ctx.heartbeat(
                stage="detecting_figures",
                stage_label="Detecting figures",
                total_units=len(drawing_pages),
                completed_units=0,
                unit="drawing pages",
                indeterminate=False,
            )

            async def complete_drawing(page_index: int) -> PageResult:
                async with worker_slots:
                    return await asyncio.to_thread(
                        complete_drawing_page,
                        pdf_bytes,
                        drawing_pages[page_index],
                        results_by_index[page_index],
                        config,
                        can_ocr,
                    )

            drawing_tasks = {
                asyncio.create_task(complete_drawing(page_index))
                for page_index in drawing_pages
            }
            all_drawing_tasks = set(drawing_tasks)
            pending_checkpoints.clear()
            completed = 0
            last_checkpoint_commit = time.monotonic()
            try:
                while drawing_tasks:
                    finished, drawing_tasks = await asyncio.wait(
                        drawing_tasks,
                        timeout=checkpoint_interval,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not finished:
                        await ctx.heartbeat(completed_units=completed)
                        continue
                    for task in finished:
                        result = await task
                        results_by_index[result.page_index] = result
                        pending_checkpoints.append(result)
                        completed += 1
                    commit_due = (
                        len(pending_checkpoints) >= _CHECKPOINT_BATCH_SIZE
                        or not drawing_tasks
                        or time.monotonic() - last_checkpoint_commit >= checkpoint_interval
                    )
                    if commit_due:
                        async with ctx.sessionmaker() as session:
                            for checkpoint in pending_checkpoints:
                                await save_page_text(
                                    session,
                                    owner_id=owner_id,
                                    source_id=source_id,
                                    cache_key=cache_key,
                                    result=checkpoint,
                                    commit=False,
                                )
                            await ctx.heartbeat_in_session(
                                session, completed_units=completed
                            )
                            await session.commit()
                        logger.info(
                            "figure detection progress job_id=%s pages=%s/%s",
                            ctx.job_id,
                            completed,
                            len(drawing_pages),
                        )
                        pending_checkpoints.clear()
                        last_checkpoint_commit = time.monotonic()
            except BaseException:
                for task in drawing_tasks:
                    task.cancel()
                await asyncio.gather(*all_drawing_tasks, return_exceptions=True)
                raise

        results = [results_by_index[page.index] for page in native_pages]

    return await asyncio.to_thread(
        assemble_from_results,
        results,
        config,
        source_sha256=source_sha256,
        doc_type=doc_type,
        total_pages=total_pages,
    )
