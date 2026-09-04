"""Grant line-reference reconstruction (DESIGN.md §12.5).

Deterministic, geometry-anchored, and shared by native and OCR words (the OCR
path just carries confidence). Real grants print line numbers in a narrow gutter
to the *right* of the column they label — the centre gutter for column 1, the
outer margin for column 2 — only every ~5th line. So:

  1. columns are detected by a coverage valley (robust to the sparse number
     tokens that defeat a naive x-gap search);
  2. the printed-number gutters are detected at the *page* level (an integer band
     that is almost all integers, distinguishing it from a body edge where
     numbered list items sit among prose) and each is assigned to the column on
     its left — so the centre gutter, which straddles the column split, is not
     miscolumned;
  3. words are split into columns (a gutter number routed to the column it
     labels) and grouped into lines within a column so side-by-side rows never
     merge;
  4. printed numbers anchor a piecewise ``y -> line`` map (interpolated between
     consecutive anchors, so heading spacing does not drift a global slope) and
     are excluded from body text; a top/bottom margin drops most header/footer.

Grant ``col:line`` only for this cut. Pure over the abstract ``Page``/``Word``
model so it is unit-testable.

ACCURACY (measured vs. a reference engine on 8 real grants, §19): the gutter is
now read directly rather than approximated — printed line numbers land within one
line on ~88-96% of lines (mean error < 1 line), up from a 2-9 line drift. The
residual is exact-match jitter of ~±1 from two remaining sources, both needing a
precise specification-boundary pass (not this module): occasional line
over-segmentation between anchors, and running-header/heading lines that leak in
above column 1. Closing that gap is corpus-guided work (§19), not single-example
tuning.
"""

from __future__ import annotations

from dataclasses import dataclass

from .artifact import Entry, Provenance
from .config import ExtractionConfig
from .locator import GrantLocator
from .model import Page, Word

_ANCHOR_MAX = 99  # a printed gutter line-number never exceeds ~70
_MIN_ANCHORS = 2  # a gutter needs at least this many increasing numbers to trust


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


def _interp(anchors: list[tuple[float, int]], y: float) -> float:
    """Piecewise-linear ``y -> printed line`` over sorted ``(y, line)`` anchors.

    Interpolates within the anchored span and extrapolates beyond it using the
    nearest segment's slope. Piecewise (vs. one global slope) tracks the uneven
    spacing around headings, where a single line would drift."""
    if y <= anchors[0][0]:
        (y0, v0), (y1, v1) = anchors[0], anchors[1]
    elif y >= anchors[-1][0]:
        (y0, v0), (y1, v1) = anchors[-2], anchors[-1]
    else:
        y0, v0 = anchors[0]
        y1, v1 = anchors[-1]
        for a, b in zip(anchors, anchors[1:], strict=False):
            if a[0] <= y <= b[0]:
                (y0, v0), (y1, v1) = a, b
                break
    if y1 == y0:
        return v0
    return v0 + (v1 - v0) * (y - y0) / (y1 - y0)


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


@dataclass(slots=True)
class _Gutter:
    """A detected printed-line-number band: which integer word carries which value,
    and the x-extent to exclude from body text."""

    value_by_word: dict[int, int]  # id(word) -> printed line number
    lo: float
    hi: float


def _increasing_words(band: list[Word]) -> list[tuple[Word, int]]:
    """Band integers in reading order, kept only while strictly increasing."""
    out: list[tuple[Word, int]] = []
    last = -1
    for w in sorted(band, key=lambda w: w.cy):
        v = int(w.text)
        if v > last:
            out.append((w, v))
            last = v
    return out


def _cluster_by_cx(ints: list[Word], *, gap: float = 0.02) -> list[list[Word]]:
    bands: list[list[Word]] = []
    cur: list[Word] = []
    for w in sorted(ints, key=lambda w: w.cx):
        if cur and w.cx - cur[-1].cx > gap:
            bands.append(cur)
            cur = []
        cur.append(w)
    if cur:
        bands.append(cur)
    return bands


def _valid_gutter_bands(words: list[Word]) -> list[tuple[float, list[tuple[Word, int]]]]:
    """Vertical bands of small integers that read like a *gutter*, not body text.

    A gutter band is almost entirely integers (high integer-to-word ratio in its
    x-strip) and increases monotonically down the page — unlike a body left edge,
    where numbered list items (``1.``, ``2.``) sit among a full column of prose
    (low ratio). Returns ``(median_cx, [(word, value), ...])`` per qualifying band."""
    ints = [w for w in words if _is_int_token(w.text)]
    if len(ints) < _MIN_ANCHORS:
        return []
    out: list[tuple[float, list[tuple[Word, int]]]] = []
    for band in _cluster_by_cx(ints):
        if len(band) < _MIN_ANCHORS:
            continue
        bx = _median([w.cx for w in band])
        density = sum(1 for w in words if abs(w.cx - bx) <= 0.02)
        if density and len(band) / density < 0.4:  # mostly prose here → a body edge
            continue
        inc = _increasing_words(band)
        if len(inc) >= _MIN_ANCHORS:
            out.append((bx, inc))
    return out


def _detect_gutters(words: list[Word], boundary: float | None) -> dict[int, _Gutter]:
    """Assign each gutter band to the column whose body sits to its left (§12.5).

    Line numbers are printed to the right of the column they label: the centre
    gutter labels column 1, the outer margin labels column 2. A single column is
    labelled by its rightmost gutter."""
    bands = _valid_gutter_bands(words)
    if not bands:
        return {}

    def make(inc: list[tuple[Word, int]]) -> _Gutter:
        return _Gutter(
            value_by_word={id(w): v for w, v in inc},
            lo=min(w.x0 for w, _ in inc),
            hi=max(w.x1 for w, _ in inc),
        )

    if boundary is None:
        cx, inc = max(bands, key=lambda b: b[0])
        return {1: make(inc)}

    gutters: dict[int, _Gutter] = {}
    left = [b for b in bands if 0.30 <= b[0] <= boundary + 0.05]
    right = [b for b in bands if b[0] > boundary + 0.05]
    if left:
        gutters[1] = make(max(left, key=lambda b: b[0])[1])
    if right:
        gutters[2] = make(max(right, key=lambda b: b[0])[1])
    return gutters


def _emit_column(
    col_words: list[Word],
    column: int,
    page_index: int,
    config: ExtractionConfig,
    ordinal: int,
    method: str,
    gutter: _Gutter | None,
) -> tuple[list[Entry], int]:
    lines = group_lines(col_words)

    values = gutter.value_by_word if gutter else {}
    band_lo = gutter.lo if gutter else 0.0
    band_hi = gutter.hi if gutter else 0.0
    pad = 0.006
    max_line = config.lines_per_column * 2

    # First pass: keep the real body lines (drop the gutter number, empty lines, and
    # a lone column-number header) and record which carry a detected anchor value.
    body_lines: list[tuple[_Line, list[Word], str, int | None]] = []
    for ln in lines:
        body = [
            w
            for w in ln.words
            if id(w) not in values and not (band_lo - pad <= w.cx <= band_hi + pad)
        ]
        text = " ".join(w.text for w in body).strip()
        if not text:
            continue
        if len(body) == 1 and _is_int_token(body[0].text):
            continue
        detected = next((values[id(w)] for w in ln.words if id(w) in values), None)
        body_lines.append((ln, body, text, detected))

    # Build the y -> printed-line anchor set from the detected gutter numbers and
    # interpolate piecewise between them, which tracks the extra spacing around
    # headings that a single global slope misses.
    anchors = sorted(
        ((ln.cy, v) for ln, _, _, v in body_lines if v is not None), key=lambda p: p[0]
    )

    entries: list[Entry] = []
    prev = 0
    for ln, body, text, detected in body_lines:
        if detected is not None:
            printed, ref_method, ref_conf = detected, "detected", "high"
        elif len(anchors) >= 2:
            printed = round(_interp(anchors, ln.cy))
            ref_method, ref_conf = "interpolated", "medium"
        else:
            printed, ref_method, ref_conf = prev + 1, "none", "low"

        printed = max(1, min(printed, max_line))
        # Line numbers are non-decreasing down a column; a wrapped line may repeat
        # the printed number (matching the page), so allow equal, never go backward.
        if printed < prev:
            printed = prev
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
    # Detect the printed-number gutters on the whole page, then assign each to the
    # column it labels — the centre gutter straddles the split, so a per-column edge
    # scan would miscolumn it (§12.5).
    gutters = _detect_gutters(words, boundary)

    if boundary is None:
        columns = [(1, words)]
    else:
        # Route each word by its geometry, except a gutter number, which goes to the
        # column it labels even when it fell on the far side of the split.
        owner = {wid: col for col, g in gutters.items() for wid in g.value_by_word}
        col1: list[Word] = []
        col2: list[Word] = []
        for w in words:
            col = owner.get(id(w)) or (1 if w.cx < boundary else 2)
            (col1 if col == 1 else col2).append(w)
        columns = [(1, col1), (2, col2)]

    entries: list[Entry] = []
    ordinal = ordinal_start
    for column, col_words in columns:
        col_entries, ordinal = _emit_column(
            col_words, column, page.index, config, ordinal, method, gutters.get(column)
        )
        entries.extend(col_entries)
    return entries, ordinal
