"""Golden harness: aggregate corpus metrics and gate them (DESIGN.md §19.1-19.2).

Runs a corpus of scored cases, aggregates the per-document measures by summing
counts (so the corpus figure is a true pooled ratio, not an average of averages),
and compares each aggregate to its §19.2 provisional release gate — producing a
pass/fail report. The gates that a synthetic seed corpus can exercise are wired
here; real labeled patents extend the same aggregate.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .metrics import CaseMetrics, Ratio


@dataclass(slots=True)
class Aggregate:
    reference_exact: Ratio = field(default_factory=Ratio.zero)
    reference_within_one: Ratio = field(default_factory=Ratio.zero)
    callout_precision: Ratio = field(default_factory=Ratio.zero)
    callout_recall: Ratio = field(default_factory=Ratio.zero)
    association_precision: Ratio = field(default_factory=Ratio.zero)
    substitution_rejection: Ratio = field(default_factory=Ratio.zero)
    box_ious: list[float] = field(default_factory=list)
    silent_verified_on_ambiguous: int = 0
    n_cases: int = 0

    @property
    def box_iou_median(self) -> float | None:
        return statistics.median(self.box_ious) if self.box_ious else None


def aggregate(cases: list[CaseMetrics]) -> Aggregate:
    agg = Aggregate(n_cases=len(cases))
    for c in cases:
        agg.reference_exact += c.reference_exact
        agg.reference_within_one += c.reference_within_one
        agg.callout_precision += c.callout_precision
        agg.callout_recall += c.callout_recall
        agg.association_precision += c.association_precision
        agg.substitution_rejection += c.substitution_rejection
        agg.box_ious.extend(c.box_ious)
        agg.silent_verified_on_ambiguous += c.silent_verified_on_ambiguous
    return agg


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    actual: float | None
    threshold: float
    passed: bool
    applicable: bool  # False when the corpus supplied no data for this measure


def _gate(
    name: str, actual: float | None, threshold: float, *, at_least: bool = True
) -> GateResult:
    if actual is None:
        return GateResult(name, None, threshold, passed=True, applicable=False)
    passed = actual >= threshold if at_least else actual <= threshold
    return GateResult(name, actual, threshold, passed=passed, applicable=True)


def evaluate_gates(agg: Aggregate) -> list[GateResult]:
    """Compare the aggregate to the §19.2 provisional gates the corpus can exercise."""
    results = [
        _gate("printed/paragraph reference exact", agg.reference_exact.value, 0.985),
        _gate("printed/paragraph reference within-one", agg.reference_within_one.value, 0.995),
        _gate("drawing callout precision", agg.callout_precision.value, 0.98),
        _gate("drawing callout recall", agg.callout_recall.value, 0.95),
        _gate("verified association precision", agg.association_precision.value, 0.99),
        _gate("external substitution rejection", agg.substitution_rejection.value, 1.0),
        _gate("entry geometry median IoU", agg.box_iou_median, 0.85),
        # A hard invariant, not a ratio: no silent verified destination on ambiguous
        # fixtures. Encoded as a <= 0 gate.
        _gate(
            "silent verified on ambiguous",
            float(agg.silent_verified_on_ambiguous),
            0.0,
            at_least=False,
        ),
    ]
    return results


def render_report(agg: Aggregate, gates: list[GateResult]) -> str:
    """A compact human-readable gate table."""
    lines = [
        f"Corpus calibration report - {agg.n_cases} case(s)",
        "=" * 64,
    ]
    width = max(len(g.name) for g in gates)
    for g in gates:
        if not g.applicable:
            status, detail = "n/a ", "no corpus data"
        else:
            status = "PASS" if g.passed else "FAIL"
            detail = f"{g.actual:.3f} (gate {g.threshold:.3f})"
        lines.append(f"  [{status}] {g.name.ljust(width)}  {detail}")
    failed = [g for g in gates if g.applicable and not g.passed]
    lines.append("=" * 64)
    lines.append("ALL GATES PASS" if not failed else f"{len(failed)} GATE(S) FAILED")
    return "\n".join(lines)
