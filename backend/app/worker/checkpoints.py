"""Resume checkpoints (DESIGN.md §10.7).

Only two checkpoint kinds exist — ``source_ready`` (preflight: page inventory)
and ``page_text`` (one page's native/OCR words or drawing callouts). Both are
persisted as ``JobCheckpoint`` rows keyed by ``source_id`` and a ``cache_key``
that binds them to the exact source bytes + engine + config + artifact schema
that produced them. A resume reuses a checkpoint only when the cache key still
matches; otherwise it recomputes. The fast downstream pipeline is never
checkpointed — it is recomputed on every run.

This module owns the (de)serialization of a :class:`PageResult` to/from the JSON
payload so the extraction core stays free of persistence concerns.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import JobCheckpoint
from ..extraction.artifact import Box, CalloutOccurrence, FigureOccurrence
from ..extraction.config import ExtractionConfig
from ..extraction.core import PageResult
from ..extraction.model import Word

SCHEMA_VERSION = 2


def resume_cache_key(
    source_sha256: str, config: ExtractionConfig, *, schema_version: int = SCHEMA_VERSION
) -> str:
    """Bind a checkpoint to the source bytes + engine + config + artifact schema."""
    payload = f"{source_sha256}|{config.version}|{config.config_hash()}|{schema_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _word_to_list(w: Word) -> list:
    return [
        w.text, w.x0, w.y0, w.x1, w.y1, w.confidence,
        w.block_num, w.paragraph_num, w.line_num, w.is_bold,
    ]


def _word_from_list(v: list) -> Word:
    return Word(
        text=v[0], x0=v[1], y0=v[2], x1=v[3], y1=v[4], confidence=v[5],
        block_num=v[6] if len(v) > 6 else None,
        paragraph_num=v[7] if len(v) > 7 else None,
        line_num=v[8] if len(v) > 8 else None,
        is_bold=v[9] if len(v) > 9 else None,
    )


def _figure_to_dict(f: FigureOccurrence) -> dict:
    return {
        "figure_id": f.figure_id,
        "page_index": f.page_index,
        "box": list(f.box),
        "confidence": f.confidence,
        "detection_score": f.detection_score,
        "method": f.method,
    }


def _figure_from_dict(d: dict) -> FigureOccurrence:
    box: Box = tuple(d["box"])  # type: ignore[assignment]
    return FigureOccurrence(
        figure_id=d["figure_id"],
        page_index=d["page_index"],
        box=box,
        confidence=d.get("confidence"),
        detection_score=d.get("detection_score"),
        method=d.get("method", "sparse_ocr"),
    )


def _callout_to_dict(c: CalloutOccurrence) -> dict:
    return {
        "callout_id": c.callout_id,
        "value": c.value,
        "page_index": c.page_index,
        "box": list(c.box),
        "figure_id": c.figure_id,
        "confidence": c.confidence,
        "detection_score": c.detection_score,
        "method": c.method,
    }


def _callout_from_dict(d: dict) -> CalloutOccurrence:
    box: Box = tuple(d["box"])  # type: ignore[assignment]
    return CalloutOccurrence(
        callout_id=d["callout_id"],
        value=d["value"],
        page_index=d["page_index"],
        box=box,
        figure_id=d.get("figure_id"),
        confidence=d.get("confidence"),
        detection_score=d.get("detection_score"),
        method=d.get("method", "sparse_ocr"),
    )


def page_result_to_payload(r: PageResult) -> dict:
    return {
        "page_index": r.page_index,
        "method": r.method,
        "is_drawing": r.is_drawing,
        "words": [_word_to_list(w) for w in r.words],
        "figures": [_figure_to_dict(f) for f in r.figures],
        "callouts": [_callout_to_dict(c) for c in r.callouts],
    }


def page_result_from_payload(d: dict) -> PageResult:
    return PageResult(
        page_index=d["page_index"],
        method=d["method"],
        is_drawing=d["is_drawing"],
        words=tuple(_word_from_list(w) for w in d.get("words", [])),
        figures=tuple(_figure_from_dict(f) for f in d.get("figures", [])),
        callouts=tuple(_callout_from_dict(c) for c in d.get("callouts", [])),
    )


async def _upsert(
    session: AsyncSession,
    *,
    owner_id: str,
    source_id: str,
    kind: str,
    page_index: int | None,
    cache_key: str,
    payload: dict,
) -> None:
    # Replace any existing row for this (source, kind, page) — a stale-key row is
    # simply overwritten so the store always reflects the current engine/config.
    await session.execute(
        delete(JobCheckpoint).where(
            JobCheckpoint.source_id == source_id,
            JobCheckpoint.kind == kind,
            JobCheckpoint.page_index == page_index,
        )
    )
    session.add(
        JobCheckpoint(
            owner_id=owner_id,
            source_id=source_id,
            kind=kind,
            page_index=page_index,
            cache_key=cache_key,
            payload=payload,
        )
    )


async def load_source_ready(
    session: AsyncSession, source_id: str, cache_key: str
) -> dict | None:
    """Return the ``source_ready`` payload when a valid one exists, else None."""
    row = await session.scalar(
        select(JobCheckpoint).where(
            JobCheckpoint.source_id == source_id,
            JobCheckpoint.kind == "source_ready",
            JobCheckpoint.cache_key == cache_key,
        )
    )
    return row.payload if row is not None else None


async def save_source_ready(
    session: AsyncSession, *, owner_id: str, source_id: str, cache_key: str, total_pages: int
) -> None:
    await _upsert(
        session,
        owner_id=owner_id,
        source_id=source_id,
        kind="source_ready",
        page_index=None,
        cache_key=cache_key,
        payload={"total_pages": total_pages},
    )
    await session.commit()


async def load_page_texts(
    session: AsyncSession, source_id: str, cache_key: str
) -> dict[int, PageResult]:
    """Return ``{page_index: PageResult}`` for all valid ``page_text`` checkpoints."""
    rows = await session.scalars(
        select(JobCheckpoint).where(
            JobCheckpoint.source_id == source_id,
            JobCheckpoint.kind == "page_text",
            JobCheckpoint.cache_key == cache_key,
        )
    )
    out: dict[int, PageResult] = {}
    for row in rows:
        result = page_result_from_payload(row.payload)
        out[result.page_index] = result
    return out


async def save_page_text(
    session: AsyncSession, *, owner_id: str, source_id: str, cache_key: str, result: PageResult
) -> None:
    await _upsert(
        session,
        owner_id=owner_id,
        source_id=source_id,
        kind="page_text",
        page_index=result.page_index,
        cache_key=cache_key,
        payload=page_result_to_payload(result),
    )
    await session.commit()
