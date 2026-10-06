"""Enrichment stage wired into the extraction worker (DESIGN.md §13).

Exercises the ``_enrich_artifact`` stage in isolation (the surrounding pipeline
needs the PDF/OCR libraries): a fetched provider page aligns ``display_text``
without touching ``source_text``, and every degradation path — enrichment
disabled, no patent identity, a fetch failure, an identity mismatch — falls back
to the unaligned artifact so extraction still publishes.
"""

from __future__ import annotations

import uuid

from app.config import get_settings
from app.db.models import ExtractionJob, User
from app.extraction.artifact import Artifact, Entry, Provenance
from app.extraction.config import DEFAULT_CONFIG
from app.extraction.locator import GrantLocator
from app.fetch.adapter import FetchResult
from app.fetch.errors import FetchError
from app.worker.engine import JobContext
from app.worker.processors import _enrich_artifact

PAGE_HTML = b"""
<html><head>
<meta name="DC.title" content="Wireless communication method">
<meta name="citation_patent_number" content="US12262260B2">
</head><body>
<section itemprop="description"><p>The housing 104 receives the shaft 108.</p></section>
</body></html>
"""


class FakeFetcher:
    def __init__(self, page: bytes, *, fail: bool = False) -> None:
        self.page = page
        self.fail = fail
        self.calls: list[str] = []

    async def fetch(self, url: str, *, max_bytes: int, expect_pdf: bool = False) -> FetchResult:
        self.calls.append(url)
        if self.fail:
            raise FetchError("boom", "fetch failed")
        return FetchResult(self.page, "text/html", url)

    async def request(self, *_args, **_kwargs) -> FetchResult:
        raise FetchError("ppubs_unavailable", "use provider fallback")


def _artifact(*texts: str) -> Artifact:
    entries = [
        Entry(
            entry_id=f"line_{i:07d}",
            ordinal=i,
            page_index=0,
            locator=GrantLocator(column=1, printed_line=i),
            box=(0.1, 0.1, 0.9, 0.12),
            source_text=t,
            display_text=t,
            provenance=Provenance(extraction_method="ocr", ocr_confidence=80.0),
        )
        for i, t in enumerate(texts, start=1)
    ]
    return Artifact(
        schema_version=1,
        source_sha256="sha",
        doc_type="grant",
        page_count=1,
        engine_version="0.1.0",
        config_hash=DEFAULT_CONFIG.config_hash(),
        mode="ocr",
        disposition="complete",
        entries=entries,
    )


async def _ctx(client) -> JobContext:
    async with client.sessionmaker() as s:
        user = User(subject=uuid.uuid4().hex, email="o@example.com")
        s.add(user)
        await s.flush()
        job = ExtractionJob(owner_id=user.id, status="running", fencing_token="tok")
        s.add(job)
        await s.commit()
        job_id = job.id
    return JobContext(
        client.sessionmaker, client.object_store,
        job_id=job_id, fencing_token="tok", lease_seconds=30,
    )


async def test_enrichment_aligns_display_text(client):
    ctx = await _ctx(client)
    art = _artifact("The houslng 104 receives the shaft 108.")  # OCR error: houslng
    fetcher = FakeFetcher(PAGE_HTML)

    out = await _enrich_artifact(
        ctx, art, config=DEFAULT_CONFIG, canonical="US12262260B2", title="t", fetcher=fetcher
    )

    assert "housing 104" in out.entries[0].display_text  # corrected
    assert out.entries[0].source_text == "The houslng 104 receives the shaft 108."  # untouched
    assert out.entries[0].provenance.identity_verified is True
    assert out.quality["identity_status"] == "verified"
    assert out.quality["aligned_lines"] == 1
    assert fetcher.calls == ["https://patents.google.com/patent/US12262260B2/en"]


async def test_enrichment_skipped_without_patent_identity(client):
    ctx = await _ctx(client)
    art = _artifact("Some uploaded text with no patent number.")
    fetcher = FakeFetcher(PAGE_HTML)

    out = await _enrich_artifact(
        ctx, art, config=DEFAULT_CONFIG, canonical=None, title=None, fetcher=fetcher
    )

    assert out is art  # unchanged, and no network was touched
    assert fetcher.calls == []


async def test_enrichment_survives_fetch_failure(client):
    ctx = await _ctx(client)
    art = _artifact("The houslng 104 receives the shaft 108.")
    fetcher = FakeFetcher(PAGE_HTML, fail=True)

    out = await _enrich_artifact(
        ctx, art, config=DEFAULT_CONFIG, canonical="US12262260B2", title="t", fetcher=fetcher
    )

    assert out is art  # graceful fallback to the unaligned artifact
    assert out.entries[0].display_text == "The houslng 104 receives the shaft 108."


async def test_enrichment_blocks_on_identity_mismatch(client):
    ctx = await _ctx(client)
    art = _artifact("The houslng 104 receives the shaft 108.")
    fetcher = FakeFetcher(PAGE_HTML)

    out = await _enrich_artifact(
        ctx, art, config=DEFAULT_CONFIG, canonical="US9999999B2", title="t", fetcher=fetcher
    )

    # Identity mismatch disables alignment: display_text stays the OCR source.
    assert out.entries[0].display_text == "The houslng 104 receives the shaft 108."
    assert out.quality["identity_status"] == "mismatch"
    assert out.quality["aligned_lines"] == 0


async def test_enrichment_respects_disabled_instance(client, monkeypatch):
    ctx = await _ctx(client)
    art = _artifact("The houslng 104 receives the shaft 108.")
    fetcher = FakeFetcher(PAGE_HTML)
    get_settings.cache_clear()
    monkeypatch.setenv("XRAY_ENRICHMENT_ENABLED", "false")

    out = await _enrich_artifact(
        ctx, art, config=DEFAULT_CONFIG, canonical="US12262260B2", title="t", fetcher=fetcher
    )

    assert out is art
    assert fetcher.calls == []
    get_settings.cache_clear()
