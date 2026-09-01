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
from typing import Dict, List, Literal, Optional, Tuple

from .locator import Locator

Disposition = Literal["complete", "complete_with_warnings", "partial", "failed"]
Confidence = Literal["high", "medium", "low"]

# Normalized top-left box: 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 (§8.2).
Box = Tuple[float, float, float, float]


def valid_box(b: Box) -> bool:
    x0, y0, x1, y1 = b
    return 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


@dataclass(frozen=True, slots=True)
class Provenance:
    extraction_method: str  # "native" | "ocr"
    ocr_confidence: Optional[float] = None
    alignment_method: Optional[str] = None  # e.g. "fuzzy"
    alignment_score: Optional[float] = None
    identity_verified: bool = False
    reference_method: Optional[str] = None  # "detected" | "interpolated" | "borrowed"
    layout_borrowed: bool = False


@dataclass(frozen=True, slots=True)
class Entry:
    """One line/span. ``entry_id`` is the stable primitive for links/bookmarks."""

    entry_id: str
    ordinal: int
    page_index: int
    locator: Locator
    box: Optional[Box]
    source_text: str
    display_text: str
    provenance: Provenance
    text_confidence: Confidence = "high"
    reference_confidence: Confidence = "high"
    section: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def ref(self) -> str:
        """Rendered citation reference (a view of the typed locator)."""
        return self.locator.render()


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
    entries: List[Entry] = field(default_factory=list)
    quality: Dict[str, object] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
