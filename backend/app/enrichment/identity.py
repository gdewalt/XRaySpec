"""Provider identity verification (DESIGN.md §13.2).

Before any display text is aligned, the source-extracted patent identity is
compared against the provider record. Only ``verified`` (or ``probable`` under
policy) permits alignment; ``mismatch`` disables it — this is what stops another
document's clean text from silently overwriting a line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_ALNUM = re.compile(r"[^A-Za-z0-9]")


@dataclass(frozen=True, slots=True)
class IdentityResult:
    status: str  # "verified" | "probable" | "mismatch" | "insufficient"
    evidence: dict


_KIND = re.compile(r"[A-Z]\d?$")


def _canon(value: str | None) -> str:
    """Country + number, kind code dropped, so ``US12262260B2`` and the provider's
    ``US:12262260`` compare equal."""
    if not value:
        return ""
    return _KIND.sub("", _ALNUM.sub("", value).upper())


def _title_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def verify_identity(
    source_canonical: str | None,
    provider_canonical: str | None,
    *,
    source_title: str | None = None,
    provider_title: str | None = None,
) -> IdentityResult:
    sc, pc = _canon(source_canonical), _canon(provider_canonical)
    title_sim = _title_similarity(source_title, provider_title)
    evidence = {
        "source_canonical": sc,
        "provider_canonical": pc,
        "title_similarity": round(title_sim, 3),
    }

    if sc and pc:
        if sc == pc:
            return IdentityResult("verified", evidence)
        return IdentityResult("mismatch", evidence)
    if title_sim >= 0.9:
        return IdentityResult("probable", evidence)
    return IdentityResult("insufficient", evidence)
