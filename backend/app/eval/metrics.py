"""Extraction accuracy metrics (DESIGN.md §19.1).

Pure scoring of an extraction :class:`Artifact` (plus optional detected callouts
for the drawing side) against a document's ground-truth :class:`DocumentLabels`.
Every function is deterministic and framework-free so the golden harness runs in
CI. Ratios are returned as ``(numerator, denominator)`` counts *and* a float, so
the corpus aggregate is a sum of counts (not an average of averages).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..extraction.artifact import Artifact, Box, CalloutOccurrence, MentionAssociation
from .labels import CalloutLabel, DocumentLabels, index_references


def iou(a: Box, b: Box) -> float:
    """Intersection-over-union of two normalized boxes."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Ratio:
    num: int
    den: int

    @property
    def value(self) -> float | None:
        return self.num / self.den if self.den else None

    def __add__(self, other: Ratio) -> Ratio:
        return Ratio(self.num + other.num, self.den + other.den)

    @classmethod
    def zero(cls) -> Ratio:
        return cls(0, 0)


@dataclass(slots=True)
class CaseMetrics:
    """Scored measures for one corpus document."""

    doc_id: str
    reference_exact: Ratio = field(default_factory=Ratio.zero)
    reference_within_one: Ratio = field(default_factory=Ratio.zero)
    callout_precision: Ratio = field(default_factory=Ratio.zero)  # TP / (TP + FP)
    callout_recall: Ratio = field(default_factory=Ratio.zero)  # TP / (TP + FN)
    association_precision: Ratio = field(default_factory=Ratio.zero)
    substitution_rejection: Ratio = field(default_factory=Ratio.zero)
    box_ious: list[float] = field(default_factory=list)
    silent_verified_on_ambiguous: int = 0  # must stay 0 (§19.2, Q20)

    @property
    def box_iou_median(self) -> float | None:
        return statistics.median(self.box_ious) if self.box_ious else None


def score_references(
    artifact: Artifact, labels: DocumentLabels
) -> tuple[Ratio, Ratio, list[float]]:
    """Printed/paragraph-reference exact & within-one accuracy, plus line-box IoUs (§19.1)."""
    by_ordinal = {e.ordinal: e for e in artifact.entries}
    wanted = index_references(labels)
    exact = 0
    within_one = 0
    ious: list[float] = []
    for ordinal, ref in wanted.items():
        entry = by_ordinal.get(ordinal)
        if entry is None:
            continue
        loc = entry.locator
        if labels.doc_type == "application":
            got = getattr(loc, "paragraph", None)
            want = ref.paragraph
        else:
            got_col = getattr(loc, "column", None)
            got = getattr(loc, "printed_line", None)
            want = ref.printed_line
            if got_col != ref.column:  # wrong column is never a match
                got = None
        if want is not None and got is not None:
            if got == want:
                exact += 1
            if abs(got - want) <= 1:
                within_one += 1
        if ref.box is not None and entry.box is not None:
            ious.append(iou(entry.box, ref.box))
    den = sum(1 for r in wanted.values() if (r.paragraph or r.printed_line) is not None)
    return Ratio(exact, den), Ratio(within_one, den), ious


def score_callouts(
    detected: list[CalloutOccurrence], labels: list[CalloutLabel], *, iou_threshold: float = 0.3
) -> tuple[Ratio, Ratio]:
    """Greedy value+IoU matching → callout detection precision and recall (§19.1)."""
    remaining = list(detected)
    tp = 0
    for want in labels:
        best_i, best_iou = -1, iou_threshold
        for i, got in enumerate(remaining):
            if got.value != want.value or got.page_index != want.page_index:
                continue
            score = iou(got.box, want.box)
            if score >= best_iou:
                best_i, best_iou = i, score
        if best_i >= 0:
            tp += 1
            remaining.pop(best_i)
    fp = len(remaining)
    fn = len(labels) - tp
    return Ratio(tp, tp + fp), Ratio(tp, tp + fn)


def score_associations(
    associations: list[MentionAssociation], labels: DocumentLabels
) -> tuple[Ratio, int]:
    """Verified-association precision, and the count of silent verified destinations
    on fixtures labeled ambiguous (which must be zero, §19.2/Q20).

    Corpus association labels are matched by numeral value: a predicted ``verified``
    is correct when a label with that value also expects ``verified``, and is a
    silent-ambiguous violation when a label with that value expects ``ambiguous``."""
    verified_values = {a.value for a in labels.associations if a.status == "verified"}
    ambiguous_values = {a.value for a in labels.associations if a.status == "ambiguous"}
    predicted_verified = [a for a in associations if a.status == "verified"]
    correct = sum(1 for a in predicted_verified if a.value in verified_values)
    silent = sum(1 for a in predicted_verified if a.value in ambiguous_values)
    return Ratio(correct, len(predicted_verified)), silent


def score_substitution_rejection(artifact: Artifact, labels: DocumentLabels) -> Ratio:
    """On a wrong-document fixture, the fraction of lines the engine left unsubstituted
    (display_text == source_text). A correct engine rejects 100% (§19.2)."""
    if not labels.wrong_document:
        return Ratio.zero()
    kept = sum(1 for e in artifact.entries if e.display_text == e.source_text)
    return Ratio(kept, len(artifact.entries))


def score_case(
    artifact: Artifact,
    labels: DocumentLabels,
    *,
    detected_callouts: list[CalloutOccurrence] | None = None,
) -> CaseMetrics:
    """Score one document across every applicable measure."""
    exact, within_one, ious = score_references(artifact, labels)
    callouts = list(detected_callouts or artifact.callout_occurrences)
    cp, cr = score_callouts(callouts, list(labels.callouts))
    ap, silent = score_associations(artifact.mention_associations, labels)
    rej = score_substitution_rejection(artifact, labels)
    return CaseMetrics(
        doc_id=labels.doc_id,
        reference_exact=exact,
        reference_within_one=within_one,
        callout_precision=cp,
        callout_recall=cr,
        association_precision=ap,
        substitution_rejection=rej,
        box_ious=ious,
        silent_verified_on_ambiguous=silent,
    )
