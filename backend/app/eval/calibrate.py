"""Threshold calibration from corpus evidence (DESIGN.md §19.2, §20).

The design forbids picking thresholds by eye: they are "recalibrated only through
an explicit decision after corpus evidence." This module is the mechanism — sweep
a config threshold across the corpus, measure the gated metric at each value, and
report the curve so the choice is made from data.

Demonstrated here on ``alignment_min_ratio`` (the clean-text substitution gate),
because it has both a benefit (more OCR lines corrected → coverage) and a risk
(substituting a wrong document's text → rejection failure), so the sweep shows a
real precision/recall-style tradeoff and a defensible recommended value. The same
``sweep`` shape applies to the grant printed-line and callout-yield knobs once the
labeled corpus of real scans exists (the two limits the README defers to §19).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..enrichment.align import align_entries
from ..extraction.artifact import Entry, Provenance
from ..extraction.config import DEFAULT_CONFIG
from ..extraction.locator import GrantLocator

# A correct-document fixture: OCR of graduated noise whose clean text is the SAME
# patent. The heavily-noised third line falls below higher thresholds, so coverage
# visibly trades off against the rejection gained by raising the threshold.
_CORRECT_CLEAN = (
    "The housing 104 receives the shaft 108. The shaft rotates freely. "
    "The bearing supports the rotor."
)
_CORRECT_OCR = [
    "The houslng 104 receives the shaft 108.",  # light noise  (~0.97)
    "The shaft rotates freely.",  # clean        (~1.00)
    "The bearlng supp0rtz the rot0r.",  # heavy noise (~0.87) — drops above 0.85
]
# A wrong-document fixture: clean text from a DIFFERENT but lexically similar
# patent, so a too-low threshold wrongly substitutes it.
_WRONG_SOURCE = ["The rotor spins about the central axis."]
_WRONG_CLEAN = "The motor spins about the central shaft."


@dataclass(frozen=True, slots=True)
class SweepPoint:
    min_ratio: float
    coverage: float  # fraction of correct-doc lines aligned (higher is better)
    rejection: float  # fraction of wrong-doc lines left unsubstituted (must be 1.0)


def _entries(texts: list[str]) -> list[Entry]:
    return [
        Entry(
            entry_id=f"line_{i:07d}",
            ordinal=i,
            page_index=0,
            locator=GrantLocator(column=1, printed_line=i + 1),
            box=(0.1, 0.1, 0.9, 0.12),
            source_text=t,
            display_text=t,
            provenance=Provenance(extraction_method="ocr", ocr_confidence=80.0),
        )
        for i, t in enumerate(texts)
    ]


def _coverage(min_ratio: float) -> float:
    config = replace(DEFAULT_CONFIG, alignment_min_ratio=min_ratio)
    aligned = align_entries(_entries(_CORRECT_OCR), _CORRECT_CLEAN, config, identity_verified=True)
    matched = sum(1 for e in aligned if e.provenance.alignment_method in ("exact", "fuzzy"))
    return matched / len(aligned)


def _rejection(min_ratio: float) -> float:
    config = replace(DEFAULT_CONFIG, alignment_min_ratio=min_ratio)
    aligned = align_entries(_entries(_WRONG_SOURCE), _WRONG_CLEAN, config, identity_verified=False)
    kept = sum(1 for e in aligned if e.display_text == e.source_text)
    return kept / len(aligned)


def calibrate_alignment_min_ratio(values: list[float]) -> list[SweepPoint]:
    """Coverage vs wrong-document rejection at each candidate threshold."""
    return [SweepPoint(v, _coverage(v), _rejection(v)) for v in sorted(values)]


def recommend_min_ratio(points: list[SweepPoint]) -> float | None:
    """The lowest threshold achieving full rejection — maximizing coverage subject
    to the hard 100%-wrong-document-rejection gate (§19.2)."""
    safe = [p for p in points if p.rejection >= 1.0]
    if not safe:
        return None
    return min(safe, key=lambda p: p.min_ratio).min_ratio


def render_sweep(points: list[SweepPoint], recommended: float | None) -> str:
    lines = [
        "alignment_min_ratio calibration (coverage vs wrong-doc rejection)",
        "  ratio   coverage   rejection",
    ]
    for p in points:
        mark = "  <- recommended" if recommended is not None and p.min_ratio == recommended else ""
        lines.append(f"  {p.min_ratio:.2f}    {p.coverage:6.2%}    {p.rejection:6.2%}{mark}")
    lines.append(
        f"recommended alignment_min_ratio = {recommended:.2f}"
        if recommended is not None
        else "no threshold achieves full rejection on this corpus"
    )
    return "\n".join(lines)
