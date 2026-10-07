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
    alignment_method: str | None = None  # "exact" | "fuzzy" | "unmatched"
    alignment_score: float | None = None
    provider: str | None = None  # alignment source, e.g. "google_patents"
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
    paragraph_start: bool = False
    paragraph_source: str | None = None  # layout | printed_marker | uspto_numbered | google_patents
    indent_level: int = 0
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
class FigureOccurrence:
    """A figure label located on a drawing page, used for direct PDF navigation."""

    figure_id: str
    page_index: int
    box: Box
    confidence: float | None = None
    detection_score: float | None = None
    method: str = "sparse_ocr"


@dataclass(frozen=True, slots=True)
class CalloutOccurrence:
    """A printed reference-numeral label on a drawing (§8.4)."""

    callout_id: str
    value: str  # normalized numeral, e.g. "104"
    page_index: int
    box: Box
    figure_id: str | None = None
    confidence: float | None = None
    detection_score: float | None = None
    method: str = "sparse_ocr"


@dataclass(frozen=True, slots=True)
class MentionAssociation:
    """A text numeral mention linked to drawing callout(s) (§8.4, §12.7)."""

    entry_id: str
    value: str
    span: tuple[int, int]
    status: str  # "verified" | "probable" | "ambiguous" | "unresolved"
    selected_callout_ids: list[str] = field(default_factory=list)
    candidate_callout_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PatentFrontMatter:
    """Provider-sourced front-page information shown before cited specification lines."""

    title: str | None = None
    patent_number: str | None = None
    abstract: str | None = None
    metadata: list[dict[str, str]] = field(default_factory=list)
    source: str | None = None


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
    figure_occurrences: list[FigureOccurrence] = field(default_factory=list)
    callout_occurrences: list[CalloutOccurrence] = field(default_factory=list)
    mention_associations: list[MentionAssociation] = field(default_factory=list)
    front_matter: PatentFrontMatter | None = None
    quality: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
