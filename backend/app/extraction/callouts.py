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
    r"([0-9ILOilo]+(?:[A-Za-z]|\s+[A-Za-z](?![A-Za-z]))?)",
    re.IGNORECASE,
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
    if _sheet_header_word_ids(words):
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
    candidates = {compact.translate(str.maketrans({"I": "1", "L": "1", "O": "0"}))}
    if len(compact) > 1 and compact[-1].isalpha():
        prefix = compact[:-1].translate(str.maketrans({"I": "1", "L": "1", "O": "0"}))
        if prefix.isdigit():
            candidates.add(f"{prefix}{compact[-1]}")
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
        for match in _FIG_REF.finditer(text):
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
    return occurrences


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
        fuzzy = next(
            (
                expected[candidate]
                for candidate in _drawing_figure_candidates(normalized)
                if candidate in expected
            ),
            None,
        )
        canonical = exact or fuzzy or normalized
        ocr = _ocr_strength(occurrence.confidence)
        if occurrence.method == "sparse_ocr_confusable":
            ocr *= 0.92
        support = 1.0 if exact else (0.82 if fuzzy else 0.0)
        score = 0.62 * ocr + 0.38 * support if support else 0.82 * ocr
        if (support and score < 0.34) or (not support and score < 0.70):
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
    return sorted(strongest.values(), key=lambda item: (item.page_index, item.figure_id))


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
