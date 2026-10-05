"""Text-side figure-reference and reference-numeral detection (DESIGN.md §12.6-12.7).

Pure analysis over the extracted entries: finds figure references (``FIG. 3``,
``FIGS. 4-6``, ``12A``) and component reference numerals (``housing 104``), with
range/list expansion and the exclusion rules that keep claim numbers, years,
measurements, ``column:line`` refs, and figure IDs from being mistaken for
reference numerals. These are the mentions the viewer renders as inline links;
association to drawing callouts (needs the drawing-side detection) is a later
slice.
"""

from __future__ import annotations

import re

from .artifact import Entry, FigureMention, NumeralMention

# --- figure references -------------------------------------------------------

_FIG_ID = r"\d+[A-Za-z]?"
_FIG_REF = re.compile(
    rf"\b(?:FIGS?|FIGURES?)\.?\s*({_FIG_ID}(?:\s*(?:[-–,]|and|to)\s*{_FIG_ID})*)",
    re.IGNORECASE,
)
_RANGE = re.compile(r"(\d+)([A-Za-z]?)\s*[-–]\s*(\d+)([A-Za-z]?)")
_SINGLE = re.compile(r"(\d+)([A-Za-z]?)")


def _expand_figure_expr(expr: str) -> list[str]:
    ids: list[str] = []
    for part in re.split(r"\s*(?:,|and)\s*", expr):
        part = part.strip()
        if not part:
            continue
        rng = _RANGE.match(part)
        if rng:
            n1, l1, n2, l2 = rng.groups()
            if n1 == n2 and l1 and l2:  # 3A-3C
                for c in range(ord(l1.upper()), ord(l2.upper()) + 1):
                    ids.append(f"{n1}{chr(c)}")
            elif not l1 and not l2:  # 4-6
                for n in range(int(n1), int(n2) + 1):
                    ids.append(str(n))
            else:  # mixed like 3A-4 — keep the endpoints
                ids.append(f"{n1}{l1.upper()}")
                ids.append(f"{n2}{l2.upper()}")
            continue
        one = _SINGLE.match(part)
        if one:
            ids.append(f"{one.group(1)}{one.group(2).upper()}")
    return ids


def detect_figure_references(entries: list[Entry]) -> list[FigureMention]:
    mentions: list[FigureMention] = []
    for entry in entries:
        for m in _FIG_REF.finditer(entry.source_text):
            ids = _expand_figure_expr(m.group(1))
            if ids:
                mentions.append(
                    FigureMention(
                        entry_id=entry.entry_id,
                        raw_text=m.group(0),
                        span=(m.start(), m.end()),
                        figure_ids=ids,
                    )
                )
    return mentions


# --- reference numerals ------------------------------------------------------

# label + numeral, e.g. "housing 104", "reference numeral 104A"
_NUMERAL = re.compile(r"\b([A-Za-z][A-Za-z-]{2,})\s+(\d{2,4}[A-Za-z]?)\b")
# a numeral coordinated onto a previous one: "104 and 106", "104, 106".
# Anchored at the given position via Pattern.match(text, pos).
_COORD = re.compile(r"\s*(?:,|and)\s+(\d{2,4}[A-Za-z]?)\b")
_UNIT = re.compile(r"^(?:%|mm|cm|km|kHz|MHz|GHz|Hz|ms|kg|nm|dB|bit|byte)s?\b", re.IGNORECASE)
# Words that are not component labels: figure/claim keywords and conjunctions/articles.
_STOPWORDS = {
    "claim", "claims", "fig", "figs", "figure", "figures", "no", "nos", "page", "pages",
    "and", "or", "nor", "the", "said", "for", "with", "from", "but", "per", "via", "a", "an",
}


def _is_excluded_numeral(value: str, label: str | None, tail: str) -> bool:
    digits = value.rstrip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if label and label.lower() in _STOPWORDS:
        return True
    if len(digits) == 4 and digits[:2] in ("19", "20") and not value[-1].isalpha():
        return True  # a year
    if _UNIT.match(tail.lstrip()):
        return True  # a measurement
    if tail[:1] == ":" or tail[:1] == ".":
        return True  # column:line or a decimal
    return False


def detect_reference_numerals(entries: list[Entry]) -> list[NumeralMention]:
    mentions: list[NumeralMention] = []
    for entry in entries:
        text = entry.source_text
        for m in _NUMERAL.finditer(text):
            label, value = m.group(1), m.group(2)
            tail = text[m.end() : m.end() + 4]
            if _is_excluded_numeral(value, label, tail):
                continue
            mentions.append(
                NumeralMention(
                    entry_id=entry.entry_id,
                    value=value,
                    component_label=label,
                    span=(m.start(2), m.end(2)),
                )
            )
            # Coordinated numerals share the label: "housings 104 and 106".
            pos = m.end()
            while True:
                c = _COORD.match(text, pos)
                if not c:
                    break
                mentions.append(
                    NumeralMention(
                        entry_id=entry.entry_id,
                        value=c.group(1),
                        component_label=label,
                        span=(c.start(1), c.end(1)),
                    )
                )
                pos = c.end()
    return mentions
