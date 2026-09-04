"""Checkpoint-based resume in the extraction worker (DESIGN.md §10.6–10.7).

Uses injected page-compute functions so the resume logic is exercised without the
PDF/OCR libraries: a first run computes and checkpoints every page; a resume
reuses valid checkpoints (skipping the expensive per-page work and even the PDF
parse); a stale cache key recomputes.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from sqlalchemy import delete, func, select

from app.db.models import ExtractionJob, JobCheckpoint, SourceDocument, User
from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import PageResult
from app.extraction.model import Page, Word
from app.worker.engine import JobContext
from app.worker.extraction import run_extraction

N_PAGES = 3


def _fake_load_pages(_pdf: bytes) -> list[Page]:
    return [Page(index=i, words=[Word("x", 0.1, 0.1, 0.2, 0.12)]) for i in range(N_PAGES)]


class Recorder:
    """A fake route_page that records how many pages it actually computed."""

    def __init__(self) -> None:
        self.pages: list[int] = []

    def __call__(self, _pdf, native_page: Page, _config, _can_ocr) -> PageResult:
        self.pages.append(native_page.index)
        # A minimal valid spec page (column header + centre gutter) so the downstream
        # assembly yields entries.
        words = (
            Word("1", 0.28, 0.045, 0.30, 0.062),
            Word("2", 0.70, 0.045, 0.72, 0.062),
            Word("housing", 0.12, 0.10, 0.30, 0.115),
            Word("right", 0.55, 0.10, 0.72, 0.115),
            Word("5", 0.49, 0.225, 0.51, 0.235),
            Word("body", 0.12, 0.225, 0.30, 0.235),
            Word("10", 0.49, 0.35, 0.51, 0.36),
            Word("more", 0.12, 0.35, 0.30, 0.36),
        )
        return PageResult(native_page.index, "ocr", is_drawing=False, words=words)


async def _ctx(client) -> tuple[JobContext, str, str]:
    async with client.sessionmaker() as s:
        user = User(subject=uuid.uuid4().hex, email="o@example.com")
        s.add(user)
        await s.flush()
        source = SourceDocument(
            owner_id=user.id, source_type="fetch", doc_type="grant",
            patent_canonical="US1B2", sha256="deadbeef", state="uploaded",
        )
        s.add(source)
        await s.flush()
        job = ExtractionJob(owner_id=user.id, status="running", fencing_token="tok")
        s.add(job)
        await s.commit()
        ctx = JobContext(
            client.sessionmaker, client.object_store,
            job_id=job.id, fencing_token="tok", lease_seconds=30,
        )
        return ctx, source.id, user.id


async def _run(client, ctx, source_id, owner_id, recorder, *, config=DEFAULT_CONFIG):
    return await run_extraction(
        ctx,
        pdf_bytes=b"%PDF-1.7 fake",
        source_id=source_id,
        owner_id=owner_id,
        config=config,
        doc_type="grant",
        load_pages=_fake_load_pages,
        route_page=recorder,
        ocr_available=lambda: True,
    )


async def _count_checkpoints(client, source_id, kind) -> int:
    async with client.sessionmaker() as s:
        return await s.scalar(
            select(func.count()).select_from(JobCheckpoint).where(
                JobCheckpoint.source_id == source_id, JobCheckpoint.kind == kind
            )
        )


async def test_first_run_computes_and_checkpoints_every_page(client):
    ctx, source_id, owner_id = await _ctx(client)
    rec = Recorder()
    art = await _run(client, ctx, source_id, owner_id, rec)

    assert sorted(rec.pages) == [0, 1, 2]  # every page computed
    assert await _count_checkpoints(client, source_id, "page_text") == N_PAGES
    assert await _count_checkpoints(client, source_id, "source_ready") == 1
    assert art.page_count == N_PAGES
    assert art.entries  # downstream produced lines


async def test_resume_reuses_all_checkpoints_and_skips_pdf_parse(client):
    ctx, source_id, owner_id = await _ctx(client)
    await _run(client, ctx, source_id, owner_id, Recorder())

    # Second run: everything is checkpointed. No page is recomputed, and load_pages
    # must not even be called — assert by making it raise if it is.
    def _boom(_pdf):
        raise AssertionError("load_pages should not run on a complete resume")

    rec2 = Recorder()
    art = await run_extraction(
        ctx, pdf_bytes=b"%PDF-1.7 fake", source_id=source_id, owner_id=owner_id,
        config=DEFAULT_CONFIG, doc_type="grant",
        load_pages=_boom, route_page=rec2, ocr_available=lambda: True,
    )
    assert rec2.pages == []  # nothing recomputed
    assert art.page_count == N_PAGES
    assert art.entries


async def test_partial_resume_recomputes_only_missing_page(client):
    ctx, source_id, owner_id = await _ctx(client)
    await _run(client, ctx, source_id, owner_id, Recorder())

    # Drop one page's checkpoint (simulate an interrupted attempt).
    async with client.sessionmaker() as s:
        await s.execute(
            delete(JobCheckpoint).where(
                JobCheckpoint.source_id == source_id,
                JobCheckpoint.kind == "page_text",
                JobCheckpoint.page_index == 1,
            )
        )
        await s.commit()

    rec2 = Recorder()
    await _run(client, ctx, source_id, owner_id, rec2)
    assert rec2.pages == [1]  # only the missing page recomputed
    assert await _count_checkpoints(client, source_id, "page_text") == N_PAGES


async def test_stale_cache_key_recomputes_all(client):
    ctx, source_id, owner_id = await _ctx(client)
    await _run(client, ctx, source_id, owner_id, Recorder())

    # A different engine version changes the cache key; old checkpoints are stale.
    other = replace(DEFAULT_CONFIG, version="9.9.9")
    rec2 = Recorder()
    await _run(client, ctx, source_id, owner_id, rec2, config=other)
    assert sorted(rec2.pages) == [0, 1, 2]  # all recomputed under the new key
    # The stale page_text rows were overwritten, not duplicated.
    assert await _count_checkpoints(client, source_id, "page_text") == N_PAGES
