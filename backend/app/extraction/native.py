"""Grant line-reference reconstruction (DESIGN.md §12.5).

Deterministic, geometry-anchored, and shared by native and OCR words (the OCR
path just carries confidence). This is a clean-model port of the reference
engine's proven approach, which reads the *printed* gutter numbers rather than
approximating them. Real grants print the line numbers **once, in the centre
gutter**, only every ~5th line (5, 10, 15, …); the two columns share one row
grid, so a centre number labels the same y-row in *both* columns. So:

  1. the printed column numbers at the page top (left N, right N+1 on one row)
     identify a specification page, its two column numbers, and the header y —
     everything above which is masthead, not specification;
  2. gutter line-number candidates are collected from a narrow centre strip and
     kept only if they are a **multiple of five in 5..70** (this alone rejects
     list items, reference numerals, years, and masthead digits), with common
     OCR digit confusions repaired first;
  3. candidates are tightened to a single x-band (a real gutter sits at one x)
     and assembled into a monotonic ``y -> line`` map whose steps form a regular
     arithmetic progression — an out-of-step number is dropped as an outlier;
  4. columns are split at the gutter x; each line's printed number is the
     piecewise-linear interpolation of the shared map at its y (so both columns
     use the same grid, and heading spacing never drifts a global slope);
  5. a page with no readable gutter but a confirmed column header falls back to a
     synthesized top->1 / bottom->N map; a page with neither is not a
     specification page and is skipped.

Grant ``col:line`` only for this cut. Pure over the abstract ``Page``/``Word``
model so it is unit-testable.

ACCURACY (measured vs. the reference engine on 8 real grants, §19): this port
agrees on the exact printed line ~98-99% of the time (within one line ~100%,
mean error < 0.1 line), at parity with the reference engine and up from a 2-9
line drift in the first clean-room attempt. The multiples-of-five gutter
constraint and arithmetic-progression cleaning are what make it robust.
"""

from __future__ import annotations

from dataclasses import dataclass

from .artifact import Entry, Provenance
from .config import ExtractionConfig
from .locator import GrantLocator
from .model import Page, Word

# Geometry (normalized page coordinates, top-left origin).
_GUTTER_HALF_WIDTH = 0.07  # centre strip: |cx - 0.5| < this holds the gutter
_GUTTER_X_SPREAD = 0.012  # a real gutter's numbers cluster within this of their median cx
_TOP_ZONE = 0.14  # column-number header lives in the top band of the page
_HEADER_PAD = 0.012  # keep content this far below the header row
_ROW_TOL = 0.01  # two tokens within this cy are on the same printed row
_MIN_INTERP = 2  # a y->line map needs at least this many anchors to interpolate
_MIN_SPEC_GUTTER = 3  # without a column pair, a spec page needs this many gutter numbers
# (two stray multiples-of-five always fit a line, so two is not enough on their own)

# Common OCR digit confusions in scanned gutter numbers (O->0, l/I->1, S->5, …).
_OCR_DIGITS = str.maketrans("OolIiSsBZGg", "00111558266")


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


def reconstruct_line_text(words: list[Word], *, preserve_tabs: bool = False) -> str:
    """Rebuild a line while retaining meaningful horizontal OCR gaps.

    Ordinary inter-word gaps remain spaces. A gap wider than roughly three
    characters is represented as one or more tabs so tabular/indented material
    is not flattened into prose.
    """
    ordered = sorted(words, key=lambda word: word.x0)
    if not ordered:
        return ""
    char_width = _median(
        [
            (word.x1 - word.x0) / max(len(word.text.strip()), 1)
            for word in ordered
            if word.x1 > word.x0 and word.text.strip()
        ]
    )
    parts = [ordered[0].text]
    for previous, word in zip(ordered, ordered[1:], strict=False):
        gap = max(0.0, word.x0 - previous.x1)
        if preserve_tabs and char_width > 0 and gap >= max(0.012, char_width * 2.75):
            tab_width = max(char_width * 4, 0.018)
            parts.append("\t" * max(1, min(4, round(gap / tab_width))))
        else:
            parts.append(" ")
        parts.append(word.text)
    return "".join(parts).strip()


def detected_indent_level(words: list[Word], common_left: float) -> int:
    """Convert a line's geometric left offset into a conservative tab count."""
    if not words:
        return 0
    offset = min(word.x0 for word in words) - common_left
    typical_height = _median([word.height for word in words if word.height > 0]) or 0.012
    indent_unit = max(0.008, typical_height * 0.65)
    if offset < indent_unit * 0.55:
        return 0
    return max(1, min(6, round(offset / indent_unit)))


def _digits(text: str) -> str | None:
    """Return the 1-3 digit integer string in ``text`` (raw or OCR-repaired), else None."""
    raw = text.strip().rstrip(".")
    for candidate in (raw, raw.translate(_OCR_DIGITS)):
        if candidate.isdigit() and 1 <= len(candidate) <= 3:
            return candidate
    return None


def _gutter_value(text: str) -> int | None:
    """A gutter line-number value: a multiple of five in 5..70 (else None)."""
    d = _digits(text)
    if d is None:
        return None
    v = int(d)
    return v if v % 5 == 0 and 5 <= v <= 70 else None


# --- line grouping -----------------------------------------------------------

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


# --- column split ------------------------------------------------------------

def _gap_boundary(words: list[Word]) -> float | None:
    centers = sorted(w.cx for w in words)
    best_gap, best_mid = 0.0, None
    for lo, hi in zip(centers, centers[1:], strict=False):
        mid = (lo + hi) / 2
        if 0.35 <= mid <= 0.65 and (hi - lo) > best_gap:
            best_gap, best_mid = hi - lo, mid
    return best_mid if best_gap >= 0.08 else None


def column_boundary(words: list[Word], *, bins: int = 60) -> float | None:
    """Detect a two-column split by a central coverage valley (gap fallback)."""
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


# --- gutter + line-number map (the reference-engine port) --------------------

def _collect_gutter(words: list[Word], header_y: float) -> list[tuple[Word, int]]:
    """Gutter number candidates in the centre strip, below the header (multiples of 5)."""
    out: list[tuple[Word, int]] = []
    for w in words:
        if w.cy <= header_y + _HEADER_PAD or abs(w.cx - 0.5) >= _GUTTER_HALF_WIDTH:
            continue
        v = _gutter_value(w.text)
        if v is not None:
            out.append((w, v))
    return out


def _filter_outliers(cands: list[tuple[Word, int]]) -> list[tuple[Word, int]]:
    """Keep only candidates whose x sits in the tight gutter band (§reference)."""
    if len(cands) < 3:
        return cands
    med_cx = _median([w.cx for w, _ in cands])
    return [(w, v) for w, v in cands if abs(w.cx - med_cx) <= _GUTTER_X_SPREAD]


def _line_map(cands: list[tuple[Word, int]]) -> list[tuple[float, int]]:
    """A monotonic ``(y, line)`` map from gutter markers, cleaned to a regular
    arithmetic progression (the printed numbers step by a constant, usually 5)."""
    pairs = sorted(((w.cy, v) for w, v in cands), key=lambda p: p[0])
    if len(pairs) < 2:
        return pairs

    steps: dict[int, int] = {}
    for (_, a), (_, b) in zip(pairs, pairs[1:], strict=False):
        if b - a > 0:
            steps[b - a] = steps.get(b - a, 0) + 1
    step = max(steps, key=lambda k: steps[k]) if steps else 5

    clean = [pairs[0]]
    for y, ln in pairs[1:]:
        py, pl = clean[-1]
        if y <= py or ln <= pl:
            continue
        gap = ln - pl
        if gap % step == 0 and 1 <= gap // step <= 3:  # allow up to 2 missed markers
            clean.append((y, ln))
    return clean


def _interp(line_map: list[tuple[float, int]], y: float) -> int:
    """Piecewise-linear ``y -> printed line``; extrapolate beyond the anchors."""
    if not line_map:
        return 1
    if len(line_map) == 1:
        return max(1, line_map[0][1])
    if y <= line_map[0][0]:
        (y0, v0), (y1, v1) = line_map[0], line_map[1]
    elif y >= line_map[-1][0]:
        (y0, v0), (y1, v1) = line_map[-2], line_map[-1]
    else:
        (y0, v0), (y1, v1) = line_map[0], line_map[-1]
        for a, b in zip(line_map, line_map[1:], strict=False):
            if a[0] <= y <= b[0]:
                (y0, v0), (y1, v1) = a, b
                break
    if y1 == y0:
        return max(1, v0)
    return max(1, round(v0 + (y - y0) / (y1 - y0) * (v1 - v0)))


def _detect_columns(words: list[Word]) -> tuple[int, int, float] | None:
    """Read the printed column numbers at the page top: a ``(N, N+1)`` pair on one
    row, left then right of centre. Returns ``(left_num, right_num, header_y)``.

    This identifies a specification page, its two column numbers, and the header y
    (everything above is masthead). Numbers sharing a row with a ``US``/kind-code
    token — the patent-number line — are excluded."""
    top = [w for w in words if w.cy < _TOP_ZONE]
    if not top:
        return None
    kind_ys = [w.cy for w in top if w.text.strip() in ("US", "A1", "A2", "B1", "B2")]

    nums: list[tuple[Word, int]] = []
    for w in top:
        if any(abs(w.cy - ky) <= _ROW_TOL for ky in kind_ys):
            continue
        d = _digits(w.text)
        if d is not None:
            nums.append((w, int(d)))

    left = [(w, n) for w, n in nums if w.cx < 0.5]
    right = [(w, n) for w, n in nums if w.cx >= 0.5]
    pairs = [
        (max(lw.cy, rw.cy), ln, rn)
        for lw, ln in left
        for rw, rn in right
        if rn == ln + 1 and abs(lw.cy - rw.cy) <= _ROW_TOL
    ]
    if not pairs:
        return None
    header_y, left_num, right_num = min(pairs, key=lambda p: p[0])
    return left_num, right_num, header_y


def _running_header_y(words: list[Word]) -> float | None:
    """Bottom baseline of a top-band US patent running header, if present.

    Some native PDFs split ``US 7,840,427 B2`` so the number/kind tokens are
    discarded independently while the bare ``US`` survives as body text. Treat
    the entire row as masthead even when the printed column-number row is not
    readable.
    """
    top = [word for word in words if word.cy < _TOP_ZONE]
    rows = group_lines(top)
    header_rows: list[float] = []
    for row in rows:
        tokens = [word.text.strip().upper() for word in row.words]
        has_us = any(token == "US" or token.startswith("US") for token in tokens)
        has_identifier = any(
            any(character.isdigit() for character in token)
            or token in {"A1", "A2", "B1", "B2"}
            for token in tokens
        )
        if has_us and has_identifier:
            header_rows.append(row.cy)
    return max(header_rows) if header_rows else None


def _synth_map(header_y: float, config: ExtractionConfig) -> list[tuple[float, int]]:
    """Fallback ``y -> line`` map when the gutter is unreadable: the column runs
    from just below the header to near the page bottom over ~``lines_per_column``."""
    return [(header_y + _HEADER_PAD, 1), (0.94, config.lines_per_column)]


# --- assembly ----------------------------------------------------------------

def _emit_column(
    col_words: list[Word],
    column: int,
    page_index: int,
    line_map: list[tuple[float, int]],
    header_cutoff: float,
    gutter_lo: float,
    gutter_hi: float,
    ordinal: int,
    method: str,
) -> tuple[list[Entry], int]:
    anchor_ys = [ay for ay, _ in line_map]
    entries: list[Entry] = []
    prev = 0

    # Preserve Tesseract's block/paragraph grouping when available. For native
    # text (and imperfect OCR), infer a conservative break from a first-line
    # indent or an unusually large vertical gap.
    content: list[tuple[_Line, list[Word], str]] = []
    for ln in group_lines(col_words):
        if ln.cy < header_cutoff:
            continue
        body = [w for w in ln.words if not (gutter_lo <= w.cx <= gutter_hi)]
        text = reconstruct_line_text(body, preserve_tabs=method == "ocr")
        if text and not (len(body) == 1 and _digits(body[0].text) is not None):
            content.append((ln, body, text))
    gaps = [
        content[i][0].cy - content[i - 1][0].cy
        for i in range(1, len(content))
        if content[i][0].cy > content[i - 1][0].cy
    ]
    typical_gap = _median(gaps)
    common_left = _median([min(w.x0 for w in body) for _, body, _ in content])

    for index, (ln, body, text) in enumerate(content):
        current_keys = {
            (w.block_num, w.paragraph_num)
            for w in body
            if w.block_num is not None and w.paragraph_num is not None
        }
        previous_keys = (
            {
                (w.block_num, w.paragraph_num)
                for w in content[index - 1][1]
                if w.block_num is not None and w.paragraph_num is not None
            }
            if index > 0 else set()
        )
        ocr_break = bool(
            index > 0 and current_keys and previous_keys and current_keys != previous_keys
        )
        vertical_gap = ln.cy - content[index - 1][0].cy if index > 0 else 0.0
        indent_level = detected_indent_level(body, common_left)
        indented = indent_level > 0
        spaced = bool(
            index > 0
            and typical_gap > 0
            and vertical_gap >= max(0.018, typical_gap * 1.6)
        )
        paragraph_start = ocr_break or indented or spaced

        printed = _interp(line_map, ln.cy)
        if printed < prev:  # non-decreasing; a wrapped line may repeat a number
            printed = prev
        prev = printed
        # A row aligned to a printed gutter number is "detected"; the rest are
        # interpolated from the map between them.
        on_anchor = len(line_map) >= 2 and any(abs(ln.cy - ay) <= _ROW_TOL for ay in anchor_ys)
        ref_method = "detected" if on_anchor else "interpolated"

        box = _clamp_box(
            min(w.x0 for w in body), min(w.y0 for w in body),
            max(w.x1 for w in body), max(w.y1 for w in body),
        )
        confidences = [w.confidence for w in body if w.confidence is not None]
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
                    ocr_confidence=(sum(confidences) / len(confidences)) if confidences else None,
                    reference_method=ref_method,
                ),
                text_confidence="high" if method == "native" else "medium",
                reference_confidence="high" if on_anchor else ("medium" if line_map else "low"),
                paragraph_start=paragraph_start,
                indent_level=indent_level,
            )
        )
        ordinal += 1
    return entries, ordinal


def extract_page(
    page: Page,
    config: ExtractionConfig,
    ordinal_start: int,
    *,
    method: str = "native",
    fallback_columns: tuple[int, int] | None = None,
) -> tuple[list[Entry], int]:
    """Reconstruct grant ``col:line`` for one specification page (§12.5).

    A page with neither a readable centre gutter nor a printed column-number pair
    is treated as non-specification (cover/front matter) and yields nothing."""
    words = page.words
    columns_hdr = _detect_columns(words)
    header_y = max(
        config.content_top_margin,
        columns_hdr[2] if columns_hdr else 0.0,
        _running_header_y(words) or 0.0,
    )

    gutter = _filter_outliers(_collect_gutter(words, header_y))
    line_map = _line_map(gutter)

    # A specification page is confirmed by a printed column-number pair, or, lacking
    # one, by a *strong* gutter (three or more centre line-numbers). Covers (which on
    # grants show a representative figure), bibliographic front matter, and drawing
    # sheets that slip past classification carry at most one or two stray
    # multiples-of-five — which always fit a line — so they are not numbered.
    strong = len(line_map) >= _MIN_SPEC_GUTTER
    if columns_hdr is None and not strong:
        return [], ordinal_start
    if len(line_map) < _MIN_INTERP:
        line_map = _synth_map(header_y, config)  # confirmed spec page, gutter unreadable
        gutter_x = 0.5
        gutter_lo = gutter_hi = -1.0  # nothing to exclude
    else:
        gutter_x = _median([w.cx for w, _ in gutter])
        gutter_lo = min(w.x0 for w, _ in gutter) - 0.004
        gutter_hi = max(w.x1 for w, _ in gutter) + 0.004

    if columns_hdr:
        detected_columns = (columns_hdr[0], columns_hdr[1])
        # Grant specification columns increase monotonically. If OCR/native
        # parsing finds a stale 1/2 pair after later columns, preserve the
        # sequence carried from the preceding specification page.
        left_num, right_num = (
            fallback_columns
            if fallback_columns and detected_columns[0] < fallback_columns[0]
            else detected_columns
        )
    else:
        left_num, right_num = fallback_columns or (1, 2)
    header_cutoff = header_y + _HEADER_PAD

    # Split the body at the gutter; drop the header band and the gutter numbers.
    body = [w for w in words if header_cutoff < w.cy <= config.content_bottom_margin]
    left_words = [w for w in body if w.cx < gutter_x - 0.005]
    right_words = [w for w in body if w.cx > gutter_x + 0.005]

    entries: list[Entry] = []
    ordinal = ordinal_start
    for column, col_words in ((left_num, left_words), (right_num, right_words)):
        if not col_words:
            continue
        col_entries, ordinal = _emit_column(
            col_words, column, page.index, line_map, header_cutoff,
            gutter_lo, gutter_hi, ordinal, method,
        )
        entries.extend(col_entries)
    return entries, ordinal
