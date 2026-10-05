"""Drawing-side callout detection + mention association (DESIGN.md §12.6-12.7).

Drawing pages carry sparse text: figure labels (``FIG. 3``) and reference-numeral
callouts (``104``) with boxes. This module:

  1. classifies a page as a drawing (sparse, numeric-heavy);
  2. detects the figure a drawing page shows (when unambiguous);
  3. extracts callout occurrences (numeral labels + boxes);
  4. associates each text numeral mention to callout(s) at the **simple tier**
     (exact value + figure context), returning verified / probable / ambiguous /
     unresolved — never a silent guess (the ambiguous case is a chooser). The
     calibrated multi-signal ranker is deferred (§12.7, Q20).

Pure over the abstract word model, unit-testable; drawing OCR is an adapter.

KNOWN LIMITATION (measured on a real scanned grant): detection + association logic
is correct and wired end to end, but real-world *callout yield* is low — Tesseract
(even in sparse-text mode) misses many small/rotated numeral labels on scanned
figures, and drawing-vs-spec page classification is approximate. Higher yield needs
bounded orientation passes, DPI/PSM tuning, and threshold calibration against the
labeled corpus (§12.7, §19) — deliberately not over-fit to a single example.
"""

from __future__ import annotations

import re

from .artifact import (
    CalloutOccurrence,
    Entry,
    FigureMention,
    FigureOccurrence,
    MentionAssociation,
    NumeralMention,
)
from .figures import _FIG_REF, _expand_figure_expr
from .model import Word
from .native import _clamp_box, group_lines

_CALLOUT = re.compile(r"^\d{2,4}[A-Za-z]?$")
_NUMERICISH = re.compile(r"^\d{1,4}[A-Za-z]?$")
_SHEET_HEADER = re.compile(r"^sheet\s+\d+\s+of\s+\d+$", re.IGNORECASE)


def _is_year(token: str) -> bool:
    return len(token) == 4 and token[:2] in ("19", "20") and token.isdigit()


def _without_sheet_headers(words: list[Word]) -> list[Word]:
    """Drop ``Sheet X of Y`` running headers before drawing-label analysis."""
    excluded: set[int] = set()
    for line in group_lines(words):
        if line.cy > 0.18:
            continue
        text = " ".join(word.text.strip() for word in line.words).strip()
        if _SHEET_HEADER.fullmatch(text):
            excluded.update(id(word) for word in line.words)
    return [word for word in words if id(word) not in excluded]


def is_drawing_page(words: list[Word], *, max_words: int = 120, min_numeric: float = 0.30) -> bool:
    """A drawing page is sparse and numeral-heavy (vs. a prose specification page)."""
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
    return occurrences


def detect_callouts(
    words: list[Word], page_index: int, figure_id: str | None
) -> list[CalloutOccurrence]:
    words = _without_sheet_headers(words)
    callouts: list[CalloutOccurrence] = []
    for i, w in enumerate(words):
        token = w.text.strip()
        if not _CALLOUT.match(token) or _is_year(token):
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
