"""Drawing-side callout detection + mention association (DESIGN.md §12.6-12.7).

Drawing pages carry sparse text: figure labels (``FIG. 3``) and reference-numeral
callouts (``104``) with boxes. This module:

  1. classifies a page as a drawing (sparse, numeric-heavy);
  2. detects the figure a drawing page shows (when unambiguous);
  3. extracts callout occurrences (numeral labels + boxes);
  4. scores candidates from OCR confidence and specification evidence; and
  5. associates each text numeral mention to callout(s), returning verified /
     probable / ambiguous / unresolved — never a silent guess (the ambiguous
     case is a chooser).

Pure over the abstract word model, unit-testable; drawing OCR is an adapter.

Drawing OCR supplies high-resolution, bounded orientation passes before this
module runs. Remaining yield/precision tuning is calibrated against the labeled
corpus (§12.7, §19), never fitted to a single example.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace

from .artifact import (
    CalloutOccurrence,
    Entry,
    FigureMention,
    FigureOccurrence,
    MentionAssociation,
    NumeralMention,
)
from .figures import _FIG_REF, _expand_figure_expr
from .model import Page, Word
from .native import _clamp_box, group_lines

_CALLOUT = re.compile(r"^\d{2,4}[A-Za-z]?$")
_NUMERICISH = re.compile(r"^\d{1,4}[A-Za-z]?$")
_SHEET_HEADER = re.compile(r"\bsheet\s+\d+\s+(?:of|/)\s+\d+\b", re.IGNORECASE)
_SPEC_TOKEN_TRIM = ".,;:()[]{}"
_DRAWING_FIG_REF = re.compile(
    r"\b(?:FIGS?|FIGURES?)\s*\.?\s*"
    r"([0-9ILOilo]+(?:[A-Za-z](?![A-Za-z])|\s+[A-Za-z](?![A-Za-z]))?)",
    re.IGNORECASE,
)
_FUZZY_DRAWING_FIG_REF = re.compile(
    # Common Tesseract damage in the label itself: F1G./FlG./FIC. and
    # FIlG. produced by widely spaced drafting lettering, and Fiqure/F1gure.
    # Keep this drawing-side only; specification references are aligned to
    # provider text and should not need such a permissive prefix.
    r"\b(?:F[1IilL|!]{1,2}[Gg6Cc][Ss5]?|[FfRrSs][iI1lL][gqG9][uoU0][rnR][eaEA3][sS5]?)"
    r"[.,;:'`]*\s*"
    r"([0-9ILOQSBZFW|iloqsbzfw]+(?:[A-Za-z](?![A-Za-z])|\s+[A-Za-z](?![A-Za-z]))?)"
)
_DRAWING_CALLOUT = re.compile(r"^[0-9ILOQSBZ]{2,4}[A-Z]?$", re.IGNORECASE)
_DIGIT_CONFUSIONS = str.maketrans(
    {"I": "1", "L": "1", "O": "0", "Q": "0", "S": "5", "B": "8", "Z": "2"}
)


def _is_year(token: str) -> bool:
    return len(token) == 4 and token[:2] in ("19", "20") and token.isdigit()


def _sheet_header_word_ids(words: list[Word]) -> set[int]:
    """Return words belonging to a top-of-page ``Sheet X of Y`` header."""
    excluded: set[int] = set()
    top_words = [word for word in words if word.cy <= 0.18]
    for line in group_lines(words):
        if line.cy > 0.18:
            continue
        text = " ".join(word.text.strip() for word in line.words).strip()
        if _SHEET_HEADER.search(text):
            excluded.update(id(word) for word in line.words)
    # Multi-orientation OCR can interleave duplicate tokens enough to disrupt the
    # phrase regex even though the authoritative native header is present. The
    # simultaneous top-band words "Sheet" and "of" are still a strong patent-
    # drawing signature; in that case discard the complete masthead band.
    top_tokens = {word.text.strip().strip(_SPEC_TOKEN_TRIM).casefold() for word in top_words}
    if "sheet" in top_tokens and "of" in top_tokens:
        excluded.update(id(word) for word in top_words if word.y0 < 0.12)
    return excluded


def _without_sheet_headers(words: list[Word]) -> list[Word]:
    """Drop the complete drawing-header band, including patent/date text."""
    excluded = _sheet_header_word_ids(words)
    if not excluded:
        return words
    header_bottom = max(word.y1 for word in words if id(word) in excluded)
    cutoff = max(0.10, header_bottom + 0.02)
    return [word for word in words if id(word) not in excluded and word.y0 >= cutoff]


def has_drawing_sheet_header(words: list[Word]) -> bool:
    """Whether positioned words contain an explicit ``Sheet X of Y`` masthead."""
    return bool(_sheet_header_word_ids(words))


def specification_callout_evidence(
    pages: Iterable[Page], *, fallback_values: Iterable[str] = ()
) -> dict[str, float]:
    """Find the bold reference numerals printed in specification columns.

    Bold native text is the strongest signal. Already-vetted textual mentions
    are retained as a softer signal even when some font metadata exists; using
    font weight as an all-or-nothing gate caused valid drawing labels to vanish
    whenever one page exposed incomplete or generic font information.
    """
    words = [word for page in pages for word in page.words]
    evidence = {
        value.strip().upper(): 0.72
        for value in fallback_values
        if _CALLOUT.fullmatch(value.strip()) and not _is_year(value.strip())
    }
    for word in words:
        # The column bounds omit running headers, page/line-number gutters, and
        # the centre gutter while retaining both prose columns.
        if not word.is_bold or not (0.075 <= word.cy <= 0.95):
            continue
        if not (0.08 <= word.cx <= 0.92) or 0.47 <= word.cx <= 0.53:
            continue
        token = word.text.strip().strip(_SPEC_TOKEN_TRIM).upper()
        if _CALLOUT.fullmatch(token) and not _is_year(token):
            evidence[token] = 1.0
    return evidence


def specification_callout_values(
    pages: Iterable[Page], *, fallback_values: Iterable[str] = ()
) -> set[str]:
    """Compatibility view of :func:`specification_callout_evidence`."""
    return set(specification_callout_evidence(pages, fallback_values=fallback_values))


def _ocr_strength(confidence: float | None) -> float:
    # Native PDF text has no OCR confidence and is more reliable than OCR.
    return 0.96 if confidence is None else max(0.0, min(1.0, confidence / 100.0))


def _callout_candidates(raw: str) -> set[str]:
    """Plausible normalized values for one drawing token.

    Tesseract commonly substitutes I/l for 1, O/Q for 0, S for 5, B for 8,
    and Z for 2. A trailing letter is also retained as a possible real suffix
    (``104A``), with the specification vocabulary deciding ambiguous cases.
    """
    candidates: set[str] = set()
    token = raw.strip().upper()
    # Sparse OCR often leaves a parenthesis/replacement glyph attached or joins
    # a short label to a neighboring letter. Analyze bounded numeric-ish runs
    # rather than requiring the whole OCR token to be clean.
    for fragment in re.findall(
        r"(?<![A-Z0-9])[0-9ILOQSBZ]{2,4}[A-Z]?(?![A-Z0-9])", token
    ):
        if not _DRAWING_CALLOUT.fullmatch(fragment) or not any(
            char.isdigit() for char in fragment
        ):
            continue
        if _CALLOUT.fullmatch(fragment) and not _is_year(fragment):
            candidates.add(fragment)
        translated = fragment.translate(_DIGIT_CONFUSIONS)
        if translated.isdigit() and 2 <= len(translated) <= 4 and not _is_year(translated):
            candidates.add(translated)
        if len(fragment) >= 3 and fragment[-1].isalpha():
            prefix = fragment[:-1].translate(_DIGIT_CONFUSIONS)
            suffixed = f"{prefix}{fragment[-1]}"
            if prefix.isdigit() and _CALLOUT.fullmatch(suffixed) and not _is_year(prefix):
                candidates.add(suffixed)
    return candidates


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _box_iou(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> float:
    ix0, iy0 = max(left[0], right[0]), max(left[1], right[1])
    ix1, iy1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def filter_callouts_by_values(
    callouts: Iterable[CalloutOccurrence], allowed_values: set[str] | Mapping[str, float]
) -> list[CalloutOccurrence]:
    """Rank drawing labels using OCR quality and specification evidence.

    Specification matches are a strong prior, not a destructive allow-list.
    High-confidence drawing-only candidates survive with a lower score so one
    missed text reference cannot erase a real callout.
    """
    if isinstance(allowed_values, Mapping):
        evidence = {value.upper(): float(score) for value, score in allowed_values.items()}
    else:
        evidence = {value.upper(): 1.0 for value in allowed_values}

    ranked: list[CalloutOccurrence] = []
    for callout in callouts:
        raw = callout.value.strip().upper()
        forms = _callout_candidates(raw)
        if not forms:
            continue
        exact = raw if raw in evidence else None
        matched = exact or max(
            forms.intersection(evidence), key=lambda value: evidence[value], default=None
        )
        edit_match = False
        if matched is None and evidence:
            distances = {
                expected: min(
                    (
                        _edit_distance(form, expected)
                        for form in forms
                        # Only repair a single missing/extra glyph here. Same-
                        # length substitutions are too easy to mis-map among a
                        # dense series such as 1201..1209; direct confusion
                        # normalization above already handles I/1, O/0, etc.
                        if abs(len(form) - len(expected)) == 1
                    ),
                    default=99,
                )
                for expected in evidence
            }
            best_distance = min(distances.values())
            closest = [value for value, distance in distances.items() if distance == best_distance]
            if best_distance == 1 and len(closest) == 1:
                matched = closest[0]
                edit_match = True
        value = matched or max(
            forms, key=lambda item: (_CALLOUT.fullmatch(item) is not None, len(item))
        )
        support = evidence.get(value, 0.0) * (0.75 if edit_match else 1.0)
        ocr = _ocr_strength(callout.confidence)
        score = 0.60 * ocr + 0.40 * support if support else 0.82 * ocr
        if support:
            method = "spec_exact" if exact else "spec_fuzzy"
        else:
            method = "drawing_only"
        # Supported candidates tolerate weak OCR; unsupported candidates must be
        # especially clear to avoid promoting arbitrary drawing dimensions.
        if (support and score < 0.34) or (not support and score < 0.70):
            continue
        ranked.append(
            replace(
                callout,
                value=value,
                detection_score=round(score, 3),
                method=method,
            )
        )

    # Multiple OCR profiles may propose different readings for the same glyph.
    # Keep the strongest spatial candidate rather than drawing stacked boxes.
    kept: list[CalloutOccurrence] = []
    for candidate in sorted(
        ranked, key=lambda item: item.detection_score or 0.0, reverse=True
    ):
        cx = (candidate.box[0] + candidate.box[2]) / 2
        cy = (candidate.box[1] + candidate.box[3]) / 2
        duplicate = any(
            existing.page_index == candidate.page_index
            and (
                _box_iou(existing.box, candidate.box) >= 0.25
                or math.hypot(
                    cx - (existing.box[0] + existing.box[2]) / 2,
                    cy - (existing.box[1] + existing.box[3]) / 2,
                ) <= 0.008
            )
            for existing in kept
        )
        if not duplicate:
            kept.append(candidate)
    return sorted(kept, key=lambda item: (item.page_index, item.box[1], item.box[0]))


def is_drawing_page(words: list[Word], *, max_words: int = 120, min_numeric: float = 0.30) -> bool:
    """A drawing page is sparse and numeral-heavy (vs. a prose specification page)."""
    # Patent drawing sheets normally identify themselves explicitly. Treat that
    # header as authoritative even when OCR also finds many labels or diagram text.
    if has_drawing_sheet_header(words):
        return True
    words = _without_sheet_headers(words)
    if len(words) > max_words:
        return False
    if not words:
        return True  # an image page with no OCR text carries no specification
    numeric = sum(1 for w in words if _NUMERICISH.match(w.text.strip()))
    return numeric / len(words) >= min_numeric


def detect_page_figure(words: list[Word]) -> str | None:
    """The single figure a drawing sheet shows, or None if zero/ambiguous."""
    words = _without_sheet_headers(words)
    text = " ".join(w.text for w in words)
    ids: set[str] = set()
    for m in _FIG_REF.finditer(text):
        ids.update(_expand_figure_expr(m.group(1)))
    return next(iter(ids)) if len(ids) == 1 else None


def _drawing_figure_candidates(raw: str) -> set[str]:
    """Normalize drawing OCR, including common I/l/1 and O/0 confusions."""
    compact = re.sub(r"\s+", "", raw).upper()
    # Thin drafting numerals are commonly read as letters. Repeated alphabetic
    # confusion glyphs (``sS`` for a single stylized 5) are another frequent
    # artifact, but repeated real digits such as ``11`` must remain intact.
    forms = {compact, re.sub(r"([A-Z])\1+", r"\1", compact)}
    translation = str.maketrans(
        {
            "I": "1", "L": "1", "|": "1", "W": "1", "O": "0", "Q": "0",
            "S": "5", "B": "8", "Z": "2", "F": "7",
        }
    )
    candidates = {form.translate(translation) for form in forms}
    for form in forms:
        if len(form) > 1 and form[-1].isalpha():
            prefix = form[:-1].translate(translation)
            if prefix.isdigit():
                candidates.add(f"{prefix}{form[-1]}")
    return {candidate for candidate in candidates if re.fullmatch(r"\d+[A-Z]?", candidate)}


def detect_figure_occurrences(words: list[Word], page_index: int) -> list[FigureOccurrence]:
    """Locate every FIG label on a drawing sheet, including multi-figure sheets."""
    words = _without_sheet_headers(words)
    occurrences: list[FigureOccurrence] = []
    seen: set[tuple[str, int, int, int, int]] = set()
    for line in group_lines(words):
        pieces: list[str] = []
        spans: list[tuple[int, int, Word]] = []
        cursor = 0
        for word in line.words:
            if pieces:
                cursor += 1
            start = cursor
            pieces.append(word.text)
            cursor += len(word.text)
            spans.append((start, cursor, word))
        text = " ".join(pieces)
        exact_matches = list(_FIG_REF.finditer(text))
        for match in exact_matches:
            matched_words = [
                word for start, end, word in spans
                if start < match.end() and end > match.start()
            ]
            if not matched_words:
                continue
            for figure_id in _expand_figure_expr(match.group(1)):
                box = _clamp_box(
                    min(w.x0 for w in matched_words),
                    min(w.y0 for w in matched_words),
                    max(w.x1 for w in matched_words),
                    max(w.y1 for w in matched_words),
                )
                key = (figure_id, *[round(value * 10000) for value in box])
                if key in seen:
                    continue
                seen.add(key)
                confidences = [w.confidence for w in matched_words if w.confidence is not None]
                occurrences.append(
                    FigureOccurrence(
                        figure_id=figure_id,
                        page_index=page_index,
                        box=box,
                        confidence=(sum(confidences) / len(confidences)) if confidences else None,
                    )
                )
        # A clean standard label is more reliable than permissive re-parsing of
        # the same glyphs.  Without this guard, ``Figure 14i`` also yielded the
        # confusable numeric candidate ``141`` at the identical location.
        if exact_matches:
            continue
        for match in _DRAWING_FIG_REF.finditer(text):
            matched_words = [
                word for start, end, word in spans
                if start < match.end() and end > match.start()
            ]
            if not matched_words:
                continue
            box = _clamp_box(
                min(w.x0 for w in matched_words),
                min(w.y0 for w in matched_words),
                max(w.x1 for w in matched_words),
                max(w.y1 for w in matched_words),
            )
            confidences = [w.confidence for w in matched_words if w.confidence is not None]
            confidence = (sum(confidences) / len(confidences)) if confidences else None
            for figure_id in _drawing_figure_candidates(match.group(1)):
                key = (figure_id, *[round(value * 10000) for value in box])
                if key in seen:
                    continue
                seen.add(key)
                occurrences.append(
                    FigureOccurrence(
                        figure_id=figure_id,
                        page_index=page_index,
                        box=box,
                        confidence=confidence,
                        method="sparse_ocr_confusable",
                    )
                )
        for match in _FUZZY_DRAWING_FIG_REF.finditer(text):
            matched_words = [
                word for start, end, word in spans
                if start < match.end() and end > match.start()
            ]
            if not matched_words:
                continue
            box = _clamp_box(
                min(w.x0 for w in matched_words),
                min(w.y0 for w in matched_words),
                max(w.x1 for w in matched_words),
                max(w.y1 for w in matched_words),
            )
            confidences = [w.confidence for w in matched_words if w.confidence is not None]
            confidence = (sum(confidences) / len(confidences)) if confidences else None
            for figure_id in _drawing_figure_candidates(match.group(1)):
                key = (figure_id, *[round(value * 10000) for value in box])
                if key in seen:
                    continue
                seen.add(key)
                occurrences.append(
                    FigureOccurrence(
                        figure_id=figure_id,
                        page_index=page_index,
                        box=box,
                        confidence=confidence,
                        method="fuzzy_prefix",
                    )
                )
    return occurrences


def _figure_order_key(figure_id: str) -> tuple[int, int] | None:
    """Natural sortable key for a base figure and optional single sub-letter."""
    match = re.fullmatch(r"(\d+)([A-Z]?)", re.sub(r"\s+", "", figure_id).upper())
    if not match:
        return None
    suffix = ord(match.group(2)) - ord("A") + 1 if match.group(2) else 0
    return int(match.group(1)), suffix


def _sequence_score_figures(
    occurrences: list[FigureOccurrence],
) -> list[FigureOccurrence]:
    """Use drawing-sheet order as confidence evidence, not an absolute rule.

    The longest non-decreasing run of figure IDs receives a small confidence
    boost. Candidates outside it receive a penalty and are removed only when
    that penalty takes an otherwise unsupported OCR label below the normal
    drawing-only threshold. Specification-supported exceptions therefore
    survive even when a patent uses an unusual ordering.
    """
    representatives: dict[tuple[int, tuple[int, int]], FigureOccurrence] = {}
    for occurrence in occurrences:
        order_key = _figure_order_key(occurrence.figure_id)
        if order_key is None:
            continue
        key = (occurrence.page_index, order_key)
        current = representatives.get(key)
        if current is None or (occurrence.detection_score or 0.0) > (
            current.detection_score or 0.0
        ):
            representatives[key] = occurrence
    ordered = sorted(
        representatives.items(), key=lambda item: (item[0][0], item[0][1])
    )
    if len(ordered) < 3:
        return occurrences

    lengths = [1] * len(ordered)
    weights = [item[1].detection_score or 0.0 for item in ordered]
    parents = [-1] * len(ordered)
    for index, ((page, order_key), _occurrence) in enumerate(ordered):
        for previous in range(index):
            (previous_page, previous_key), _ = ordered[previous]
            # Repeated recognition of the same identifier on a later sheet is
            # not sequence progress. Requiring strict figure order lets the
            # surrounding run distinguish a real FIGURE 40 from a high-quality
            # OCR misread as a second FIGURE 5.
            if previous_page > page or previous_key >= order_key:
                continue
            candidate = (
                lengths[previous] + 1,
                weights[previous] + (ordered[index][1].detection_score or 0.0),
            )
            if candidate > (lengths[index], weights[index]):
                lengths[index], weights[index] = candidate
                parents[index] = previous

    # Sequence length is decisive. For equal-length paths prefer the lower
    # natural figure identifier, then the earlier terminal sheet, before OCR
    # confidence. This prevents both a distant ``99`` outlier and a confidently
    # hallucinated repeat near the end from displacing the coherent run.
    cursor = max(
        range(len(ordered)),
        key=lambda i: (
            lengths[i],
            -ordered[i][0][1][0],
            -ordered[i][0][1][1],
            -ordered[i][0][0],
            weights[i],
        ),
    )
    sequence_keys: set[tuple[int, tuple[int, int]]] = set()
    while cursor >= 0:
        sequence_keys.add(ordered[cursor][0])
        cursor = parents[cursor]

    def sequence_violation(page: int, order_key: tuple[int, int]) -> int:
        previous = [key for candidate_page, key in sequence_keys if candidate_page < page]
        following = [key for candidate_page, key in sequence_keys if candidate_page > page]
        violation = 0
        if previous and order_key < max(previous):
            violation = max(violation, max(previous)[0] - order_key[0])
        if following and order_key > min(following):
            violation = max(violation, order_key[0] - min(following)[0])
        return violation

    rescored: list[FigureOccurrence] = []
    for occurrence in occurrences:
        order_key = _figure_order_key(occurrence.figure_id)
        if order_key is None:
            rescored.append(occurrence)
            continue
        in_sequence = (occurrence.page_index, order_key) in sequence_keys
        violation = 0 if in_sequence else sequence_violation(occurrence.page_index, order_key)
        # Patent sheets sometimes place adjacent figures out of numerical order
        # (for example 2/4 on one sheet and 3/5 on the next). Treat those small
        # inversions gently while still rejecting a distant number hallucinated
        # in the middle of an otherwise strong run.
        adjustment = 0.04 if in_sequence else (-0.04 if violation <= 2 else -0.20)
        score = max(
            0.0,
            min(1.0, (occurrence.detection_score or 0.0) + adjustment),
        )
        supported = occurrence.method.startswith("spec_")
        if score < (0.34 if supported else 0.70):
            continue
        rescored.append(replace(occurrence, detection_score=round(score, 3)))

    # When one identifier appears on several sheets, prefer its occurrence in
    # the coherent sequence. This is the final guard against a caption such as
    # FIGURE 40 being OCR'd as FIGURE 5 after the genuine FIGURE 5 was already
    # seen near the beginning of the drawings.
    sequence_by_id = {
        occurrence.figure_id
        for occurrence in rescored
        if (
            (key := _figure_order_key(occurrence.figure_id)) is not None
            and (occurrence.page_index, key) in sequence_keys
        )
    }
    return [
        occurrence
        for occurrence in rescored
        if occurrence.figure_id not in sequence_by_id
        or (
            (key := _figure_order_key(occurrence.figure_id)) is not None
            and (occurrence.page_index, key) in sequence_keys
        )
    ]


def filter_figure_occurrences(
    occurrences: Iterable[FigureOccurrence], expected_ids: set[str]
) -> list[FigureOccurrence]:
    """Score and canonicalize drawing labels against specification references.

    Expected identifiers substantially raise confidence but do not form a hard
    allow-list. A clear label can therefore survive when the specification OCR
    missed its textual reference.
    """
    expected = {re.sub(r"\s+", "", value).upper(): value.upper() for value in expected_ids}
    strongest: dict[tuple[int, str], FigureOccurrence] = {}
    for occurrence in occurrences:
        normalized = re.sub(r"\s+", "", occurrence.figure_id).upper()
        exact = expected.get(normalized)
        fuzzy_candidates = _drawing_figure_candidates(normalized)
        # OCR frequently glues the first letter of an adjacent word to a
        # numeric caption (``FIGURE 2PRIOR``) or mistakes the final digit for a
        # letter (``50Q``). Consider the numeric base only while matching
        # specification evidence so genuine subfigures remain distinct.
        trailing = re.fullmatch(r"(\d+)[A-Z]", normalized)
        if trailing:
            fuzzy_candidates.add(trailing.group(1))
        fuzzy = next(
            (
                expected[candidate]
                for candidate in fuzzy_candidates
                if candidate in expected
            ),
            None,
        )
        canonical = exact or fuzzy or normalized
        ocr = 1.0 if occurrence.method == "native_text" else _ocr_strength(occurrence.confidence)
        if occurrence.method == "sparse_ocr_confusable":
            ocr *= 0.92
        elif occurrence.method == "fuzzy_prefix":
            ocr *= 0.82
        support = 1.0 if exact else (0.82 if fuzzy else 0.0)
        score = 0.62 * ocr + 0.38 * support if support else 0.82 * ocr
        minimum_supported = 0.30 if fuzzy and not exact else 0.34
        if (support and score < minimum_supported) or (not support and score < 0.70):
            continue
        match_method = "spec_exact" if exact else ("spec_fuzzy" if fuzzy else "drawing_only")
        candidate = replace(
            occurrence,
            figure_id=canonical,
            detection_score=round(score, 3),
            method=match_method,
        )
        key = (candidate.page_index, canonical)
        current = strongest.get(key)
        if current is None or (candidate.detection_score or 0.0) > (
            current.detection_score or 0.0
        ):
            strongest[key] = candidate
    ordered = sorted(strongest.values(), key=lambda item: (item.page_index, item.figure_id))

    # A tiny first-sheet caption is a common one-off OCR error (``FIGURE 1``
    # read as ``FIGURE 2``). Correct it only when the specification starts with
    # that missing identifier and the apparent next identifier is independently
    # present on a later sheet. The duplicate is the corroboration; page order
    # alone is not enough to rewrite a label.
    expected_keys = sorted(
        key for value in expected_ids if (key := _figure_order_key(value)) is not None
    )
    observed_keys = [_figure_order_key(item.figure_id) for item in ordered]
    if expected_keys:
        first_key = expected_keys[0]
        next_key = (first_key[0] + 1, 0)
        duplicates = [
            index for index, key in enumerate(observed_keys) if key == next_key
        ]
        if first_key not in observed_keys and len(duplicates) >= 2:
            earliest_index = min(duplicates, key=lambda index: ordered[index].page_index)
            earliest = ordered[earliest_index]
            if (
                earliest.page_index == min(item.page_index for item in ordered)
                and (earliest.confidence or 0.0) < 45.0
            ):
                canonical_first = expected.get(str(first_key[0]), str(first_key[0]))
                ordered[earliest_index] = replace(
                    earliest,
                    figure_id=canonical_first,
                    method="spec_sequence_corrected",
                )
    return sorted(
        _sequence_score_figures(ordered),
        key=lambda item: (item.page_index, _figure_order_key(item.figure_id) or (0, 0)),
    )


def infer_missing_figure_occurrences(
    occurrences: Iterable[FigureOccurrence],
    drawing_sheet_pages: Iterable[int],
    expected_ids: set[str],
) -> list[FigureOccurrence]:
    """Recover specification-supported labels omitted by drawing OCR.

    Patent drawings commonly put several consecutive figures on one sheet and
    sometimes hand-letter a caption that Tesseract cannot read.  Strong labels
    on neighboring sheets still provide useful, bounded evidence.  Inference is
    limited to identifiers explicitly found in the specification and to gaps
    enclosed by drawing-sheet anchors; it never invents a new figure number.
    """
    result = list(occurrences)
    sheet_pages = sorted(set(drawing_sheet_pages))
    if not sheet_pages:
        return result

    expected_by_key = {
        key: re.sub(r"\s+", "", figure_id).upper()
        for figure_id in expected_ids
        if (key := _figure_order_key(figure_id)) is not None
    }

    def state() -> tuple[dict[int, list[FigureOccurrence]], set[tuple[int, int]]]:
        by_page: dict[int, list[FigureOccurrence]] = {}
        detected: set[tuple[int, int]] = set()
        for occurrence in result:
            key = _figure_order_key(occurrence.figure_id)
            if key is None:
                continue
            by_page.setdefault(occurrence.page_index, []).append(occurrence)
            detected.add(key)
        return by_page, detected

    def score_from(*groups: list[FigureOccurrence]) -> float:
        strengths = [
            max((item.detection_score or 0.0 for item in group), default=0.0)
            for group in groups
            if group
        ]
        return round(min(0.65, min(strengths, default=0.55) * 0.65), 3)

    def add(page_index: int, key: tuple[int, int], score: float) -> None:
        nonlocal result
        figure_id = expected_by_key.get(key)
        if figure_id is None or any(
            _figure_order_key(item.figure_id) == key for item in result
        ):
            return
        result.append(
            FigureOccurrence(
                figure_id=figure_id,
                page_index=page_index,
                # There is no label box to highlight; this tiny valid sentinel is
                # ignored by the viewer for sequence-inferred navigation targets.
                box=(0.4999, 0.1000, 0.5001, 0.1002),
                confidence=None,
                detection_score=score,
                method="sequence_inferred",
            )
        )

    # First retain the original high-certainty rule: one missing identifier per
    # blank sheet between exact natural-order anchors (including 14A/14B/14C).
    by_page, detected_keys = state()
    index = 0
    while index < len(sheet_pages):
        if sheet_pages[index] in by_page:
            index += 1
            continue
        run_start = index
        while index < len(sheet_pages) and sheet_pages[index] not in by_page:
            index += 1
        run_pages = sheet_pages[run_start:index]
        if run_start == 0 or index >= len(sheet_pages):
            continue
        previous_items = by_page.get(sheet_pages[run_start - 1], [])
        next_items = by_page.get(sheet_pages[index], [])
        previous_keys = [
            key for item in previous_items if (key := _figure_order_key(item.figure_id))
        ]
        next_keys = [
            key for item in next_items if (key := _figure_order_key(item.figure_id))
        ]
        if not previous_keys or not next_keys:
            continue
        lower, upper = max(previous_keys), min(next_keys)
        candidates = [
            key for key in sorted(expected_by_key)
            if lower < key < upper and key not in detected_keys
        ]
        if len(candidates) == len(run_pages):
            score = score_from(previous_items, next_items)
            for page_index, key in zip(run_pages, candidates, strict=True):
                add(page_index, key, score)

    # A blank run can represent several captions per sheet. Distribute missing
    # numeric groups proportionally between the surrounding anchors, keeping all
    # subfigures of the same base number on the same sheet.
    by_page, detected_keys = state()
    index = 0
    while index < len(sheet_pages):
        if sheet_pages[index] in by_page:
            index += 1
            continue
        run_start = index
        while index < len(sheet_pages) and sheet_pages[index] not in by_page:
            index += 1
        run_pages = sheet_pages[run_start:index]
        if run_start == 0 or index >= len(sheet_pages):
            # A leading sheet is still bounded by the document's first expected
            # figure and the first detected label. Require one numeric group per
            # sheet so a weak first-page guess cannot fan out.
            if run_start == 0 and index < len(sheet_pages):
                next_items = by_page.get(sheet_pages[index], [])
                next_keys = [
                    key for item in next_items
                    if (key := _figure_order_key(item.figure_id))
                ]
                if next_keys:
                    candidates = [
                        key for key in sorted(expected_by_key)
                        if key < min(next_keys) and key not in detected_keys
                    ]
                    bases = sorted({key[0] for key in candidates})
                    if len(bases) == len(run_pages):
                        score = score_from(next_items)
                        for page_index, base in zip(run_pages, bases, strict=True):
                            for key in candidates:
                                if key[0] == base:
                                    add(page_index, key, score)
            continue

        previous_items = by_page.get(sheet_pages[run_start - 1], [])
        next_items = by_page.get(sheet_pages[index], [])
        previous_bases = [
            key[0] for item in previous_items
            if (key := _figure_order_key(item.figure_id))
        ]
        next_bases = [
            key[0] for item in next_items
            if (key := _figure_order_key(item.figure_id))
        ]
        if not previous_bases or not next_bases:
            continue
        lower, upper = max(previous_bases), min(next_bases)
        if lower >= upper:
            continue
        candidates = [
            key for key in sorted(expected_by_key)
            if lower < key[0] < upper and key not in detected_keys
        ]
        bases = sorted({key[0] for key in candidates})
        if not bases:
            continue
        score = score_from(previous_items, next_items)
        sheet_span = len(run_pages) + 1
        number_span = upper - lower
        for base in bases:
            offset = int(((base - lower) * sheet_span / number_span) + 0.5)
            offset = min(len(run_pages), max(1, offset))
            for key in candidates:
                if key[0] == base:
                    add(run_pages[offset - 1], key, score)

    # A populated sheet can begin a short missing run and an immediately
    # following blank sheet can finish it. This covers interleaved layouts such
    # as 50/52-56 on one sheet followed by a hand-lettered 51 on the next.
    by_page, detected_keys = state()
    for position in range(1, len(sheet_pages) - 2):
        previous_page = sheet_pages[position - 1]
        current_page = sheet_pages[position]
        if previous_page not in by_page or current_page not in by_page:
            continue
        next_position = position + 1
        while next_position < len(sheet_pages) and sheet_pages[next_position] not in by_page:
            next_position += 1
        blank_pages = sheet_pages[position + 1 : next_position]
        if not blank_pages or next_position >= len(sheet_pages):
            continue
        next_page = sheet_pages[next_position]
        previous_items = by_page[previous_page]
        current_items = by_page[current_page]
        next_items = by_page[next_page]
        previous_bases = [
            key[0] for item in previous_items
            if (key := _figure_order_key(item.figure_id))
        ]
        current_bases = [
            key[0] for item in current_items
            if (key := _figure_order_key(item.figure_id))
        ]
        next_bases = [
            key[0] for item in next_items
            if (key := _figure_order_key(item.figure_id))
        ]
        if not previous_bases or not current_bases or not next_bases:
            continue
        if min(next_bases) != max(current_bases) + 1:
            continue
        candidates = [
            key for key in sorted(expected_by_key)
            if max(previous_bases) < key[0] < min(current_bases)
            and key not in detected_keys
        ]
        bases = sorted({key[0] for key in candidates})
        if len(bases) != len(blank_pages) + 1:
            continue
        score = score_from(previous_items, current_items, next_items)
        target_pages = [current_page, *blank_pages]
        for page_index, base in zip(target_pages, bases, strict=True):
            for key in candidates:
                if key[0] == base:
                    add(page_index, key, score)

    # Some layouts deliberately continue an earlier numerical group on the next
    # otherwise blank sheet. If the following sheet begins exactly after the
    # prior sheet's range, assign a single specification-supported hole to that
    # intervening blank sheet.
    by_page, detected_keys = state()
    for position in range(1, len(sheet_pages) - 1):
        page = sheet_pages[position]
        if page in by_page:
            continue
        previous_items = by_page.get(sheet_pages[position - 1], [])
        next_items = by_page.get(sheet_pages[position + 1], [])
        previous_bases = sorted(
            {
                key[0] for item in previous_items
                if (key := _figure_order_key(item.figure_id))
            }
        )
        next_bases = sorted(
            {
                key[0] for item in next_items
                if (key := _figure_order_key(item.figure_id))
            }
        )
        if not previous_bases or not next_bases or next_bases[0] != previous_bases[-1] + 1:
            continue
        holes = [
            key for key in sorted(expected_by_key)
            if previous_bases[0] <= key[0] <= previous_bases[-1]
            and key not in detected_keys
        ]
        if len({key[0] for key in holes}) == 1:
            score = score_from(previous_items, next_items)
            for key in holes:
                add(page, key, score)

    # Finally fill internal holes on a multi-figure sheet, except when that ID
    # was already recovered elsewhere (important for interleaved 2/4 and 3/5
    # layouts on adjacent sheets).
    by_page, detected_keys = state()
    for page_index, items in by_page.items():
        bases = sorted(
            {
                key[0] for item in items
                if (key := _figure_order_key(item.figure_id))
            }
        )
        if len(bases) < 2:
            continue
        score = score_from(items)
        for key in sorted(expected_by_key):
            if bases[0] < key[0] < bases[-1] and key not in detected_keys:
                add(page_index, key, score)

    # Consecutive populated sheets can still have a short numerical gap when a
    # caption on the first sheet is handwritten or omitted by OCR. Assign those
    # intervening specification IDs to the preceding sheet; a longer interleaved
    # run with a blank sheet was already handled above.
    by_page, detected_keys = state()
    for current_page, next_page in zip(sheet_pages, sheet_pages[1:], strict=False):
        current_items = by_page.get(current_page, [])
        next_items = by_page.get(next_page, [])
        if not current_items or not next_items:
            continue
        current_bases = [
            key[0] for item in current_items
            if (key := _figure_order_key(item.figure_id))
        ]
        next_bases = [
            key[0] for item in next_items
            if (key := _figure_order_key(item.figure_id))
        ]
        if not current_bases or not next_bases or max(current_bases) >= min(next_bases):
            continue
        score = score_from(current_items, next_items)
        for key in sorted(expected_by_key):
            if (
                max(current_bases) < key[0] < min(next_bases)
                and key not in detected_keys
            ):
                add(current_page, key, score)

    # If the specification refers to a base figure as well as its subfigures,
    # let the base navigate to the first detected subfigure sheet.
    by_page, detected_keys = state()
    for key in sorted(expected_by_key):
        if key[1] != 0 or key in detected_keys:
            continue
        subfigures = [
            item
            for items in by_page.values()
            for item in items
            if (
                (item_key := _figure_order_key(item.figure_id)) is not None
                and item_key[0] == key[0]
                and item_key[1] > 0
            )
        ]
        if subfigures:
            first = min(subfigures, key=lambda item: item.page_index)
            add(first.page_index, key, score_from([first]))

    return sorted(
        result,
        key=lambda item: (item.page_index, _figure_order_key(item.figure_id) or (0, 0)),
    )


def assign_callouts_to_figures(
    callouts: Iterable[CalloutOccurrence], figures: Iterable[FigureOccurrence]
) -> list[CalloutOccurrence]:
    """Assign each drawing numeral to the nearest supported figure on its page."""
    by_page: dict[int, list[FigureOccurrence]] = {}
    for figure in figures:
        by_page.setdefault(figure.page_index, []).append(figure)
    assigned: list[CalloutOccurrence] = []
    for callout in callouts:
        candidates = by_page.get(callout.page_index, [])
        if not candidates:
            assigned.append(callout)
            continue
        existing = next(
            (
                figure
                for figure in candidates
                if callout.figure_id
                and figure.figure_id.upper() == callout.figure_id.upper()
            ),
            None,
        )
        if existing is not None:
            assigned.append(replace(callout, figure_id=existing.figure_id))
            continue
        cx = (callout.box[0] + callout.box[2]) / 2
        cy = (callout.box[1] + callout.box[3]) / 2
        closest = min(
            candidates,
            key=lambda figure: math.hypot(
                cx - (figure.box[0] + figure.box[2]) / 2,
                cy - (figure.box[1] + figure.box[3]) / 2,
            ),
        )
        assigned.append(replace(callout, figure_id=closest.figure_id))
    return assigned


def detect_callouts(
    words: list[Word],
    page_index: int,
    figure_id: str | None,
    figure_occurrences: Iterable[FigureOccurrence] = (),
) -> list[CalloutOccurrence]:
    words = _without_sheet_headers(words)
    figure_boxes = [figure.box for figure in figure_occurrences if figure.page_index == page_index]
    callouts: list[CalloutOccurrence] = []
    for i, w in enumerate(words):
        token = w.text.strip().strip(_SPEC_TOKEN_TRIM).upper()
        if not _callout_candidates(token):
            continue
        # The numeric part of "FIG. 14B" is a navigation label, not a component
        # callout. Its center falls inside the combined figure-label box.
        if any(box[0] <= w.cx <= box[2] and box[1] <= w.cy <= box[3] for box in figure_boxes):
            continue
        callouts.append(
            CalloutOccurrence(
                callout_id=f"callout_{page_index:04d}_{i:04d}",
                value=token,
                page_index=page_index,
                box=_clamp_box(w.x0, w.y0, w.x1, w.y1),
                figure_id=figure_id,
                confidence=w.confidence,
            )
        )
    return callouts


def _figure_context(
    mention: NumeralMention, figure_mentions: list[FigureMention], ordinal_of: dict[str, int]
) -> set[str]:
    """Figures in scope for a mention: same-entry figure refs, else the nearest
    preceding figure reference."""
    same = [fm for fm in figure_mentions if fm.entry_id == mention.entry_id]
    if same:
        return {fid for fm in same for fid in fm.figure_ids}
    mo = ordinal_of.get(mention.entry_id, -1)
    preceding = [fm for fm in figure_mentions if ordinal_of.get(fm.entry_id, -1) <= mo]
    if not preceding:
        return set()
    last = max(preceding, key=lambda fm: ordinal_of.get(fm.entry_id, -1))
    return set(last.figure_ids)


def associate_mentions(
    mentions: list[NumeralMention],
    callouts: list[CalloutOccurrence],
    figure_mentions: list[FigureMention],
    entries: list[Entry],
) -> list[MentionAssociation]:
    ordinal_of = {e.entry_id: e.ordinal for e in entries}
    by_value: dict[str, list[CalloutOccurrence]] = {}
    for c in callouts:
        by_value.setdefault(c.value, []).append(c)

    results: list[MentionAssociation] = []
    for mention in mentions:
        matching = by_value.get(mention.value, [])
        context = _figure_context(mention, figure_mentions, ordinal_of)
        in_context = [c for c in matching if c.figure_id in context] if context else []
        pool = in_context or matching

        if len(pool) == 1:
            status = "verified" if in_context else "probable"
            selected = [pool[0].callout_id]
            candidates = [pool[0].callout_id]
        elif len(pool) > 1:
            status = "ambiguous"
            selected = []
            candidates = [c.callout_id for c in pool]
        else:
            status = "unresolved"
            selected = []
            candidates = []

        results.append(
            MentionAssociation(
                entry_id=mention.entry_id,
                value=mention.value,
                span=mention.span,
                status=status,
                selected_callout_ids=selected,
                candidate_callout_ids=candidates,
            )
        )
    return results
