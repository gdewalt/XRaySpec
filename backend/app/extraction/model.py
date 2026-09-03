"""Abstract page/word model for extraction (DESIGN.md §12).

The extraction algorithm operates on this normalized, library-agnostic model, not
on pdfplumber objects — so the geometry logic is unit-testable on synthetic pages
and the PDF parser is a thin, swappable adapter (``app.extraction.pdf``).

Coordinates are normalized to [0, 1] with a top-left origin (y increases
downward), matching the artifact box convention (§8.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True, slots=True)
class Page:
    index: int
    words: list[Word] = field(default_factory=list)
