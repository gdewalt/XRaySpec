"""Grant line-reference reconstruction (DESIGN.md §12.5).

Deterministic, geometry-anchored, and shared by native and OCR words (the OCR
path just carries confidence). Real two-column patents number **each column
independently at its own left edge** (column 1 far-left, column 2 at the left
edge of the right column), so:

  1. columns are detected by a coverage valley (robust to the sparse number
     tokens that defeat a naive x-gap search);
  2. words are split into columns and grouped into lines *within* a column so
     side-by-side rows never merge;
  3. within each column, the left-edge gutter integers are fit to a ``y -> line``
     model and excluded from body text;
  4. a top/bottom margin drops the running header and page-number footer.

Grant ``col:line`` only for this cut. Pure over the abstract ``Page``/``Word``
model so it is unit-testable; real-patent accuracy is a corpus (§19) concern.

KNOWN LIMITATION (measured on a real scanned grant): column separation is solid,
but printed-line-number *precision* is not yet. Many two-column grants print the
line numbers in the *center gutter*, only every ~5th line, with each column
numbered over its own range — which "leftmost token per column" cannot capture,
so those pages fall back to sequential per-column numbering (readable, correctly
ordered text with approximate ``line`` values). Robust center-gutter number
association is deliberately deferred to corpus-guided calibration (§19) rather
than over-fit to a single example.
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


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _is_int_token(text: str) -> bool:
    t = text.strip()
    return t.isdigit() and 1 <= int(t) <= _ANCHOR_MAX


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


def _gap_boundary(words: list[Word]) -> float | None:
    """Two-column split as the widest x-gap near the centre (sparse pages)."""
    centers = sorted(w.cx for w in words)
    best_gap, best_mid = 0.0, None
    for lo, hi in zip(centers, centers[1:], strict=False):
        mid = (lo + hi) / 2
        if 0.35 <= mid <= 0.65 and (hi - lo) > best_gap:
            best_gap, best_mid = hi - lo, mid
    return best_mid if best_gap >= 0.08 else None


def column_boundary(words: list[Word], *, bins: int = 60) -> float | None:
    """Detect a two-column split.

    Dense pages use a coverage valley (a low-density central band even when a few
    left-of-column-2 number tokens sit in it); sparse pages fall back to the widest
    central x-gap.
    """
    if len(words) < 20:
        return _gap_boundary(words)

    counts = [0] * bins
    for w in words:
        lo = max(0, min(bins - 1, int(w.x0 * bins)))
        hi = max(0, min(bins - 1, int(w.x1 * bins)))
        for b in range(lo, hi + 1):
            counts[b] += 1

    left = _median(counts[int(0.10 * bins) : int(0.45 * bins)])
    right = _median(counts[int(0.55 * bins) : int(0.90 * bins)])
    if left <= 0 or right <= 0:
        return None
    central = range(int(0.40 * bins), int(0.60 * bins) + 1)
    valley = min(central, key=lambda b: counts[b])
    if counts[valley] <= 0.4 * (left + right) / 2:
        return (valley + 0.5) / bins
    return None


def _clamp_box(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    x0, x1 = max(0.0, min(x0, 1.0)), max(0.0, min(x1, 1.0))
    y0, y1 = max(0.0, min(y0, 1.0)), max(0.0, min(y1, 1.0))
    if x1 <= x0:
        x1 = min(1.0, x0 + 1e-4)
    if y1 <= y0:
        y1 = min(1.0, y0 + 1e-4)
    return x0, y0, x1, y1


def _emit_column(
    col_words: list[Word],
    column: int,
    page_index: int,
    config: ExtractionConfig,
    ordinal: int,
    method: str,
) -> tuple[list[Entry], int]:
    lines = group_lines(col_words)

    # Left-edge gutter anchors: a line whose *leftmost* token is a small integer,
    # kept only while strictly increasing down the column.
    anchors: list[tuple[_Line, Word, int]] = []
    last = -1
    for ln in sorted(lines, key=lambda ln: ln.cy):
        if ln.words and _is_int_token(ln.words[0].text):
            value = int(ln.words[0].text)
            if value > last:
                anchors.append((ln, ln.words[0], value))
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
            printed, ref_method, ref_conf = anchor_value[id(ln)], "detected", "high"
        elif fit is not None:
            a, b = fit
            printed, ref_method, ref_conf = round(a * ln.cy + b), "interpolated", "medium"
        else:
            printed, ref_method, ref_conf = prev + 1, "none", "low"

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
        confidences = [w.confidence for w in body if w.confidence is not None]
        ocr_confidence = (sum(confidences) / len(confidences)) if confidences else None
        entries.append(
            Entry(
                entry_id=f"line_{ordinal:07d}",
                ordinal=ordinal,
                page_index=page_index,
                locator=GrantLocator(column=column, printed_line=printed),
                box=box,
                source_text=text,
                display_text=text,
                provenance=Provenance(
                    extraction_method=method,
                    ocr_confidence=ocr_confidence,
                    reference_method=ref_method,
                ),
                text_confidence="high" if method == "native" else "medium",
                reference_confidence=ref_conf,
            )
        )
        ordinal += 1
    return entries, ordinal


def extract_page(
    page: Page, config: ExtractionConfig, ordinal_start: int, *, method: str = "native"
) -> tuple[list[Entry], int]:
    # Drop running header / footer margins (§12.5).
    words = [
        w for w in page.words if config.content_top_margin <= w.cy <= config.content_bottom_margin
    ]

    boundary = column_boundary(words)
    if boundary is None:
        columns = [(1, words)]
    else:
        columns = [
            (1, [w for w in words if w.cx < boundary]),
            (2, [w for w in words if w.cx >= boundary]),
        ]

    entries: list[Entry] = []
    ordinal = ordinal_start
    for column, col_words in columns:
        col_entries, ordinal = _emit_column(
            col_words, column, page.index, config, ordinal, method
        )
        entries.extend(col_entries)
    return entries, ordinal
