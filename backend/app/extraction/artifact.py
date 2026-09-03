"""Immutable extraction artifact domain model (DESIGN.md §8).

Plain frozen dataclasses, framework-free, so the extraction core (§25.3.1) stays
pure. The API serializes these via ``app.schemas`` (Pydantic); persistence stores
the large arrays as immutable blobs in object storage with queryable summaries in
Postgres (§7.1).

Only the load-bearing shapes are modeled here; figure/callout indices (§8.3–8.4)
are added when Phase 3 lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .locator import Locator

Disposition = Literal["complete", "complete_with_warnings", "partial", "failed"]
Confidence = Literal["high", "medium", "low"]

# Normalized top-left box: 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 (§8.2).
Box = tuple[float, float, float, float]


def valid_box(b: Box) -> bool:
    x0, y0, x1, y1 = b
    return 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


@dataclass(frozen=True, slots=True)
class Provenance:
    extraction_method: str  # "native" | "ocr"
    ocr_confidence: float | None = None
    alignment_method: str | None = None  # e.g. "fuzzy"
    alignment_score: float | None = None
    identity_verified: bool = False
    reference_method: str | None = None  # "detected" | "interpolated" | "borrowed"
    layout_borrowed: bool = False


@dataclass(frozen=True, slots=True)
class Entry:
    """One line/span. ``entry_id`` is the stable primitive for links/bookmarks."""

    entry_id: str
    ordinal: int
    page_index: int
    locator: Locator
    box: Box | None
    source_text: str
    display_text: str
    provenance: Provenance
    text_confidence: Confidence = "high"
    reference_confidence: Confidence = "high"
    section: str | None = None
    warnings: list[str] = field(default_factory=list)

    def ref(self) -> str:
        """Rendered citation reference (a view of the typed locator)."""
        return self.locator.render()


@dataclass(frozen=True, slots=True)
class FigureMention:
    """A textual figure reference (``FIG. 3``, ``FIGS. 4-6``) and its expansion (§8.3)."""

    entry_id: str
    raw_text: str
    span: tuple[int, int]  # half-open char offsets in source_text
    figure_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NumeralMention:
    """A component reference numeral in prose (``housing 104``) (§8.4)."""

    entry_id: str
    value: str  # normalized numeral, e.g. "104" or "104A"
    component_label: str | None
    span: tuple[int, int]  # half-open char offsets in source_text


@dataclass(frozen=True, slots=True)
class Artifact:
    """Immutable result of one extraction run (DESIGN.md §8.1)."""

    schema_version: int
    source_sha256: str
    doc_type: Literal["grant", "application"]
    page_count: int
    engine_version: str
    config_hash: str
    mode: Literal["native", "ocr", "hybrid"]
    disposition: Disposition
    entries: list[Entry] = field(default_factory=list)
    figure_mentions: list[FigureMention] = field(default_factory=list)
    numeral_mentions: list[NumeralMention] = field(default_factory=list)
    quality: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
