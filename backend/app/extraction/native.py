"""Native grant line-reference reconstruction (DESIGN.md §12.5).

Deterministic, geometry-anchored. Groups words into lines by an adaptive
baseline, detects the printed gutter line-numbers, fits a robust ``y -> line``
model, and interpolates line numbers for the rest with monotonic clamping. Grant
``col:line`` only for this first cut; OCR fallback, applications (paragraphs),
figures, and callouts are later slices.

Pure over the abstract ``Page``/``Word`` model so it is unit-testable on
synthetic pages. Real-patent accuracy is measured against the labeled corpus
(§19), not asserted here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .artifact import Entry, Provenance
from .config import ExtractionConfig
from .locator import GrantLocator
from .model import Page, Word

_ANCHOR_MAX = 99  # a printed gutter line-number never exceeds ~70


@dataclass(slots=True)
class _Line:
    words: list[Word]
    cy: float
    x0: float
    x1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def group_lines(words: list[Word], *, tol_ratio: float = 0.6) -> list[_Line]:
    """Group words into text lines by vertical proximity (adaptive baseline)."""
    if not words:
        return []
    med_h = _median([w.height for w in words if w.height > 0]) or 0.01
    tol = max(med_h * tol_ratio, 1e-4)

    ordered = sorted(words, key=lambda w: (w.cy, w.x0))
    groups: list[list[Word]] = [[ordered[0]]]
    running_cy = ordered[0].cy
    for w in ordered[1:]:
        if abs(w.cy - running_cy) <= tol:
            groups[-1].append(w)
            running_cy = sum(x.cy for x in groups[-1]) / len(groups[-1])
        else:
            groups.append([w])
            running_cy = w.cy

    lines: list[_Line] = []
    for g in groups:
        ws = sorted(g, key=lambda w: w.x0)
        lines.append(
            _Line(
                words=ws,
                cy=sum(w.cy for w in ws) / len(ws),
                x0=min(w.x0 for w in ws),
                x1=max(w.x1 for w in ws),
            )
        )
    return sorted(lines, key=lambda ln: ln.cy)


def _fit(anchors: list[tuple[float, int]]) -> tuple[float, float] | None:
    """Least-squares fit of ``line = a*y + b`` over (y, line) anchors."""
    if len({y for y, _ in anchors}) < 2:
        return None
    n = len(anchors)
    sy = sum(y for y, _ in anchors)
    sl = sum(v for _, v in anchors)
    syy = sum(y * y for y, _ in anchors)
    syl = sum(y * v for y, v in anchors)
    denom = n * syy - sy * sy
    if denom == 0:
        return None
    a = (n * syl - sy * sl) / denom
    b = (sl - a * sy) / n
    return a, b


def column_boundary(words: list[Word]) -> float | None:
    """Detect a two-column split as the widest x-gap near the page centre."""
    centers = sorted(w.cx for w in words)
    best_gap, best_mid = 0.0, None
    for lo, hi in zip(centers, centers[1:], strict=False):
        mid = (lo + hi) / 2
        if 0.35 <= mid <= 0.65 and (hi - lo) > best_gap:
            best_gap, best_mid = hi - lo, mid
    return best_mid if best_gap >= 0.08 else None


def _clamp_box(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    x0, x1 = max(0.0, min(x0, 1.0)), max(0.0, min(x1, 1.0))
    y0, y1 = max(0.0, min(y0, 1.0)), max(0.0, min(y1, 1.0))
    if x1 <= x0:
        x1 = min(1.0, x0 + 1e-4)
    if y1 <= y0:
        y1 = min(1.0, y0 + 1e-4)
    return x0, y0, x1, y1


def _extract_column(
    lines: list[_Line], column: int, page_index: int, config: ExtractionConfig, ordinal: int
) -> tuple[list[Entry], int]:
    # Candidate gutter anchors: a line whose leftmost token is a small integer,
    # kept only while strictly increasing down the page.
    anchors: list[tuple[_Line, Word, int]] = []
    last = -1
    candidates = [
        (ln, ln.words[0]) for ln in lines if ln.words and ln.words[0].text.strip().isdigit()
    ]
    for ln, lead in sorted(candidates, key=lambda c: c[0].cy):
        value = int(lead.text)
        if 1 <= value <= _ANCHOR_MAX and value > last:
            anchors.append((ln, lead, value))
            last = value

    band_hi = max((lead.x1 for _, lead, _ in anchors), default=0.0)
    fit = _fit([(ln.cy, v) for ln, _, v in anchors])
    anchor_value = {id(ln): v for ln, _, v in anchors}
    max_line = config.lines_per_column * 2

    entries: list[Entry] = []
    prev = 0
    for ln in lines:
        body = [w for w in ln.words if w.x1 > band_hi + 1e-6]
        text = " ".join(w.text for w in body).strip()
        if not text:
            continue

        if id(ln) in anchor_value:
            printed, method, ref_conf = anchor_value[id(ln)], "detected", "high"
        elif fit is not None:
            a, b = fit
            printed, method, ref_conf = round(a * ln.cy + b), "interpolated", "medium"
        else:
            printed, method, ref_conf = prev + 1, "none", "low"

        printed = max(1, min(printed, max_line))
        if printed <= prev:
            printed = prev + 1
        prev = printed

        box = _clamp_box(
            min(w.x0 for w in body),
            min(w.y0 for w in body),
            max(w.x1 for w in body),
            max(w.y1 for w in body),
        )
        entries.append(
            Entry(
                entry_id=f"line_{ordinal:07d}",
                ordinal=ordinal,
                page_index=page_index,
                locator=GrantLocator(column=column, printed_line=printed),
                box=box,
                source_text=text,
                display_text=text,
                provenance=Provenance(extraction_method="native", reference_method=method),
                text_confidence="high",
                reference_confidence=ref_conf,
            )
        )
        ordinal += 1
    return entries, ordinal


def extract_page(
    page: Page, config: ExtractionConfig, ordinal_start: int
) -> tuple[list[Entry], int]:
    # Split columns *first* (each column has its own ~65-line baseline grid), then
    # group lines within a column so side-by-side columns don't merge (§12.5).
    boundary = column_boundary(page.words)
    if boundary is None:
        columns = [(1, page.words)]
    else:
        columns = [
            (1, [w for w in page.words if w.cx < boundary]),
            (2, [w for w in page.words if w.cx >= boundary]),
        ]

    entries: list[Entry] = []
    ordinal = ordinal_start
    for column, col_words in columns:
        col_lines = group_lines(col_words)
        col_entries, ordinal = _extract_column(col_lines, column, page.index, config, ordinal)
        entries.extend(col_entries)
    return entries, ordinal
