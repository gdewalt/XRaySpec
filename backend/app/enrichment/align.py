"""Clean-text alignment (DESIGN.md §13.3).

Aligns each extracted line's noisy ``source_text`` (native or OCR) to the
authoritative clean text fetched from the provider, and sets ``display_text`` to
the clean version — **without ever overwriting ``source_text``** (§3, §25.1). A
line that cannot be matched confidently keeps its source text (substitution is
rejected per line, not just per document, §13.3). Geometry, locators, and
provenance are preserved; only ``display_text`` and the alignment provenance
change.

Pure over strings/entries so it is unit-testable; the provider fetch is an
adapter. A forward cursor keeps the match monotonic (small backtrack allowed) to
resist spurious jumps.
"""

from __future__ import annotations

import re
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Any

from ..extraction.artifact import Entry
from ..extraction.config import ExtractionConfig
from .google_text import extract_provider_text
from .identity import IdentityResult, verify_identity
from .ppubs_text import extract_ppubs_text

_NONALNUM = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")
_EDGE_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[/\-'’][A-Za-z0-9]+)*")


def _normalize(text: str) -> str:
    return _WS.sub(" ", _NONALNUM.sub(" ", text.lower())).strip()


def _normalized_whitespace_tokens(text: str) -> list[str]:
    """Normalize while retaining the provider's one-token-per-space model.

    Normalizing a whole line before splitting turns ``and/or`` into two tokens,
    while provider tokenization retains it as one normalized token (``and or``).
    Keeping those models consistent prevents punctuation-heavy line boundaries
    from throwing off the forward alignment cursor.
    """
    return [normalized for token in text.split() if (normalized := _normalize(token))]


def _clean_tokens(clean_text: str) -> tuple[list[str], list[str], list[int]]:
    """Return original/normalized tokens plus their provider paragraph indexes."""
    original: list[str] = []
    normalized: list[str] = []
    paragraphs: list[int] = []
    for paragraph_index, paragraph in enumerate(clean_text.splitlines()):
        for tok in paragraph.split():
            norm = _normalize(tok)
            if norm:
                original.append(tok)
                normalized.append(norm)
                paragraphs.append(paragraph_index)
    return original, normalized, paragraphs


def _best_window(
    clean_norm: list[str], cursor: int, n: int, src_join: str, slack: int, *, backtrack: int = 2
) -> tuple[float, int, int]:
    best_ratio, best_start, best_end = 0.0, cursor, cursor
    lo = max(0, cursor - backtrack)
    hi = min(len(clean_norm), cursor + n + slack)
    for start in range(lo, hi):
        for length in (n - 1, n, n + 1):
            if length <= 0:
                continue
            end = start + length
            if end > len(clean_norm):
                continue
            window = " ".join(clean_norm[start:end])
            ratio = SequenceMatcher(None, src_join, window).ratio()
            if ratio > best_ratio:
                best_ratio, best_start, best_end = ratio, start, end
    return best_ratio, best_start, best_end


def _is_truncated_repeat(
    previous: str,
    current: str,
    *,
    allow_short: bool = False,
) -> bool:
    """Return true for a clipped repeat such as ``transport`` / ``ansport``.

    Column OCR can recognize the same word at the end of one row and again at
    the start of the next, with one or two leading characters clipped by the
    crop. Requiring a long common suffix keeps ordinary repeated or inflected
    words intact.
    """
    left = "".join(_normalize(previous).split())
    right = "".join(_normalize(current).split())
    if not left or not right:
        return False
    if left == right:
        # Exact duplicates are ambiguous without provider evidence. At an
        # aligned→unmatched boundary, however, a substantial repeated word is
        # the characteristic remainder of a hyphenated OCR line.
        return allow_short and len(left) >= 5
    if len(right) > len(left):
        return False
    if allow_short:
        # Provider alignment gives us a strong additional signal: the next
        # authoritative token is not this fragment. This covers severe page-
        # edge clipping such as ``Interface`` / ``ace``, ``or`` / ``r``, and
        # ``12`` / ``2`` without applying the same broad rule to ordinary
        # source-only word pairs. Some crops delete an interior character
        # (``interface`` / ``iterface``), so a near-full fuzzy match is also
        # accepted here.
        if left.endswith(right):
            return True
        # OCR can damage the first character of the carried-over fragment
        # (``waiting`` / ``vaiting``). Compare it with the equally sized suffix
        # rather than requiring a literal suffix relationship.
        suffix = left[-len(right) :]
        if len(right) >= 4 and SequenceMatcher(None, suffix, right).ratio() >= 0.82:
            return True
        return bool(
            len(left) >= 5
            and len(right) >= 4
            and len(right) < len(left)
            and len(left) - len(right) <= 2
            and SequenceMatcher(None, left, right).ratio() >= 0.86
        )
    if len(left) < 7:
        return False
    if len(right) < 5 or len(left) - len(right) > 2:
        return False
    suffix = 0
    for left_char, right_char in zip(reversed(left), reversed(right), strict=False):
        if left_char != right_char:
            break
        suffix += 1
    return suffix >= max(5, (len(right) * 7 + 9) // 10)


def strip_leading_line_overlap(
    previous_text: str,
    current_text: str,
    *,
    allow_short: bool = False,
) -> str:
    """Remove one OCR-clipped repeat from the beginning of the next line."""
    previous_words = _EDGE_TOKEN.findall(previous_text)
    first = _EDGE_TOKEN.search(current_text)
    if not previous_words or first is None or _normalize(current_text[: first.start()]):
        return current_text
    after = current_text[first.end() :]
    separator = re.match(r"^[.,;:!?\"'’”()\[\]]*(?:\s+|$)", after)
    if separator is None:
        return current_text
    if not _is_truncated_repeat(
        previous_words[-1], first.group(0), allow_short=allow_short
    ):
        return current_text
    leading_space = re.match(r"^\s*", current_text).group(0)
    return f"{leading_space}{after[separator.end():]}"


def repair_serialized_display_overlaps(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Repair legacy artifact display text without mutating its stored source.

    This compatibility view makes already-published artifacts benefit from the
    overlap fix. Only consecutive lines on the same page and in the same grant
    column are considered.
    """
    repaired: list[dict[str, Any]] = []
    for raw in entries:
        current = raw
        if repaired:
            previous = repaired[-1]
            previous_locator = previous.get("locator") or {}
            current_locator = raw.get("locator") or {}
            same_flow = (
                raw.get("ordinal") == previous.get("ordinal", -2) + 1
                and raw.get("page_index") == previous.get("page_index")
                and current_locator.get("kind") == previous_locator.get("kind")
                and current_locator.get("column") == previous_locator.get("column")
            )
            if same_flow:
                provenance = raw.get("provenance") or {}
                previous_provenance = previous.get("provenance") or {}
                provider_boundary = (
                    provenance.get("alignment_method") == "unmatched"
                    and previous_provenance.get("alignment_method") in {"exact", "fuzzy"}
                )
                display = strip_leading_line_overlap(
                    str(previous.get("display_text", "")),
                    str(raw.get("display_text", "")),
                    # A rejected provider substitution means this line fell
                    # back to its OCR/native source. At that boundary it is
                    # safe to recognize a shorter clipped suffix such as
                    # ``Interface`` / ``ace``.
                    allow_short=provider_boundary,
                )
                if display != raw.get("display_text"):
                    current = {**raw, "display_text": display}
        repaired.append(current)
    return repaired


def align_entries(
    entries: list[Entry],
    clean_text: str,
    config: ExtractionConfig,
    *,
    identity_verified: bool,
    provider: str = "google_patents",
    provider_paragraph_source: str | None = None,
) -> list[Entry]:
    """Return new entries with ``display_text`` aligned to ``clean_text``."""
    original, normalized, paragraphs = _clean_tokens(clean_text)
    if not normalized:
        return entries

    cursor = 0
    previous_paragraph: int | None = None
    out: list[Entry] = []
    for entry in entries:
        src_norm = _normalized_whitespace_tokens(entry.source_text)
        if not src_norm:
            out.append(entry)
            continue

        ratio, start, end = _best_window(
            normalized, cursor, len(src_norm), " ".join(src_norm), config.alignment_search_slack
        )
        # If the crop repeated the previous provider word with its first one or
        # two characters missing, align the rest strictly forward. This avoids
        # both keeping the fragment and pulling the complete word into two rows.
        clipped_leading_repeat = bool(
            cursor > 0
            and len(src_norm) > 1
            and _is_truncated_repeat(
                normalized[cursor - 1], src_norm[0], allow_short=True
            )
            # If the clean provider repeats the word too, it is legitimate
            # prose (for example, ``surface face``), not OCR overlap.
            and (cursor >= len(normalized) or normalized[cursor] != src_norm[0])
        )
        if clipped_leading_repeat:
            trimmed_ratio, trimmed_start, trimmed_end = _best_window(
                normalized,
                cursor,
                len(src_norm) - 1,
                " ".join(src_norm[1:]),
                config.alignment_search_slack,
                backtrack=0,
            )
            if trimmed_ratio >= config.alignment_min_ratio:
                ratio, start, end = trimmed_ratio, trimmed_start, trimmed_end
        if ratio >= config.alignment_min_ratio:
            display = " ".join(original[start:end])
            method = "exact" if ratio >= config.alignment_exact_ratio else "fuzzy"
            cursor = end
            paragraph = paragraphs[start]
            provider_paragraph_start = bool(
                provider_paragraph_source
                and (
                    (previous_paragraph is None and paragraph > 0)
                    or (previous_paragraph is not None and paragraph != previous_paragraph)
                )
            )
            if provider_paragraph_source:
                # Provider paragraphs are authoritative for spacing. Do not
                # preserve layout/OCR guesses inside one provider paragraph.
                paragraph_start = provider_paragraph_start
                paragraph_source = (
                    provider_paragraph_source if provider_paragraph_start else None
                )
            else:
                paragraph_start = entry.paragraph_start
                paragraph_source = entry.paragraph_source
            previous_paragraph = paragraph
        else:
            display = entry.source_text  # reject substitution; keep the source line
            same_flow = bool(
                out
                and out[-1].page_index == entry.page_index
                and out[-1].locator.kind == entry.locator.kind
                and (
                    entry.locator.kind != "grant"
                    or out[-1].locator.column == entry.locator.column
                )
            )
            if same_flow:
                display = strip_leading_line_overlap(
                    out[-1].display_text,
                    display,
                    allow_short=clipped_leading_repeat,
                )
            method = "unmatched"
            if provider_paragraph_source:
                paragraph_start = entry.paragraph_source == provider_paragraph_source
                paragraph_source = entry.paragraph_source if paragraph_start else None
            else:
                paragraph_start = entry.paragraph_start
                paragraph_source = entry.paragraph_source

        provenance = replace(
            entry.provenance,
            alignment_method=method,
            alignment_score=round(ratio, 3),
            provider=provider,
            identity_verified=identity_verified,
        )
        out.append(
            replace(
                entry,
                display_text=display,
                provenance=provenance,
                paragraph_start=paragraph_start,
                paragraph_source=paragraph_source,
            )
        )
    return out


def enrich_from_page_html(
    entries: list[Entry],
    page_html: str,
    source_canonical: str | None,
    config: ExtractionConfig,
    *,
    source_title: str | None = None,
    allow_probable: bool = False,
) -> tuple[list[Entry], IdentityResult]:
    """Parse the provider page, verify identity, and align — the worker's entry
    point (§13). Falls back to the unaligned entries when identity is not trusted
    or the provider text is missing (graceful degradation)."""
    provider = extract_provider_text(page_html)
    identity = verify_identity(
        source_canonical, provider.canonical,
        source_title=source_title, provider_title=provider.title,
    )
    allowed = identity.status == "verified" or (identity.status == "probable" and allow_probable)
    if not allowed or not provider.clean_text:
        return entries, identity
    aligned = align_entries(
        entries,
        provider.clean_text,
        config,
        identity_verified=identity.status == "verified",
        provider="google_patents",
        provider_paragraph_source="google_patents",
    )
    return aligned, identity


def enrich_from_ppubs_html(
    entries: list[Entry],
    page_html: str,
    source_canonical: str | None,
    config: ExtractionConfig,
    *,
    source_title: str | None = None,
    allow_probable: bool = False,
) -> tuple[list[Entry], IdentityResult]:
    """Verify and align against USPTO text, including its paragraph boundaries."""
    provider = extract_ppubs_text(page_html)
    identity = verify_identity(
        source_canonical,
        provider.canonical,
        source_title=source_title,
        provider_title=provider.title,
    )
    allowed = identity.status == "verified" or (
        identity.status == "probable" and allow_probable
    )
    if not allowed or not provider.clean_text:
        return entries, identity
    aligned = align_entries(
        entries,
        provider.clean_text,
        config,
        identity_verified=identity.status == "verified",
        provider="uspto_ppubs",
        provider_paragraph_source="uspto_numbered",
    )
    return aligned, identity
