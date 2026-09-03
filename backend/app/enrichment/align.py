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

from ..extraction.artifact import Entry
from ..extraction.config import ExtractionConfig
from .google_text import extract_provider_text
from .identity import IdentityResult, verify_identity

_NONALNUM = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", _NONALNUM.sub(" ", text.lower())).strip()


def _clean_tokens(clean_text: str) -> tuple[list[str], list[str]]:
    """Return parallel (original, normalized) token lists, dropping punctuation-only tokens."""
    original: list[str] = []
    normalized: list[str] = []
    for tok in clean_text.split():
        norm = _normalize(tok)
        if norm:
            original.append(tok)
            normalized.append(norm)
    return original, normalized


def _best_window(
    clean_norm: list[str], cursor: int, n: int, src_join: str, slack: int
) -> tuple[float, int, int]:
    best_ratio, best_start, best_end = 0.0, cursor, cursor
    lo = max(0, cursor - 2)
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


def align_entries(
    entries: list[Entry],
    clean_text: str,
    config: ExtractionConfig,
    *,
    identity_verified: bool,
    provider: str = "google_patents",
) -> list[Entry]:
    """Return new entries with ``display_text`` aligned to ``clean_text``."""
    original, normalized = _clean_tokens(clean_text)
    if not normalized:
        return entries

    cursor = 0
    out: list[Entry] = []
    for entry in entries:
        src_norm = _normalize(entry.source_text).split()
        if not src_norm:
            out.append(entry)
            continue

        ratio, start, end = _best_window(
            normalized, cursor, len(src_norm), " ".join(src_norm), config.alignment_search_slack
        )
        if ratio >= config.alignment_min_ratio:
            display = " ".join(original[start:end])
            method = "exact" if ratio >= config.alignment_exact_ratio else "fuzzy"
            cursor = end
        else:
            display = entry.source_text  # reject substitution; keep the source line
            method = "unmatched"

        provenance = replace(
            entry.provenance,
            alignment_method=method,
            alignment_score=round(ratio, 3),
            provider=provider,
            identity_verified=identity_verified,
        )
        out.append(replace(entry, display_text=display, provenance=provenance))
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
        entries, provider.clean_text, config, identity_verified=identity.status == "verified"
    )
    return aligned, identity
