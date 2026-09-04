"""Golden-harness accuracy metrics (DESIGN.md §19.1)."""

from __future__ import annotations

from app.eval.labels import CalloutLabel, DocumentLabels, ReferenceLabel
from app.eval.metrics import (
    Ratio,
    iou,
    score_callouts,
    score_references,
)
from app.extraction.artifact import Artifact, CalloutOccurrence, Entry, Provenance
from app.extraction.locator import ApplicationLocator, GrantLocator


def _grant_entry(ordinal: int, column: int, line: int, box) -> Entry:
    return Entry(
        entry_id=f"l{ordinal}", ordinal=ordinal, page_index=0,
        locator=GrantLocator(column=column, printed_line=line),
        box=box, source_text="x", display_text="x",
        provenance=Provenance(extraction_method="native"),
    )


def _artifact(entries) -> Artifact:
    return Artifact(
        schema_version=2, source_sha256="s", doc_type="grant", page_count=1,
        engine_version="0", config_hash="h", mode="native", disposition="complete",
        entries=list(entries),
    )


def test_iou_identical_and_disjoint():
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1.0
    assert iou((0, 0, 0.1, 0.1), (0.5, 0.5, 0.6, 0.6)) == 0.0
    assert abs(iou((0, 0, 2, 2), (1, 1, 3, 3)) - (1 / 7)) < 1e-9


def test_reference_exact_and_within_one():
    box = (0.1, 0.1, 0.3, 0.12)
    entries = [
        _grant_entry(0, 1, 1, box),   # exact
        _grant_entry(1, 1, 3, box),   # off by one from labeled 2 -> within_one only
        _grant_entry(2, 2, 9, box),   # wrong column -> neither
    ]
    labels = DocumentLabels(
        doc_id="d", doc_type="grant",
        references=(
            ReferenceLabel(0, column=1, printed_line=1, box=box),
            ReferenceLabel(1, column=1, printed_line=2, box=box),
            ReferenceLabel(2, column=1, printed_line=9, box=box),
        ),
    )
    exact, within_one, ious = score_references(_artifact(entries), labels)
    assert exact == Ratio(1, 3)
    assert within_one == Ratio(2, 3)  # line 1 exact + line 2 within one
    assert len(ious) == 3 and all(v == 1.0 for v in ious)


def test_reference_application_paragraph():
    e = Entry(
        entry_id="l0", ordinal=0, page_index=0,
        locator=ApplicationLocator(paragraph=42),
        box=None, source_text="x", display_text="x",
        provenance=Provenance(extraction_method="native"),
    )
    art = _artifact([e])
    labels = DocumentLabels(
        doc_id="d", doc_type="application",
        references=(ReferenceLabel(0, paragraph=42),),
    )
    exact, within_one, _ = score_references(art, labels)
    assert exact == Ratio(1, 1) and within_one == Ratio(1, 1)


def test_callout_precision_recall_with_iou_and_value():
    detected = [
        CalloutOccurrence("c1", "104", 0, (0.20, 0.30, 0.24, 0.33)),  # TP
        CalloutOccurrence("c2", "106", 0, (0.55, 0.40, 0.59, 0.43)),  # TP
        CalloutOccurrence("c3", "999", 0, (0.10, 0.10, 0.14, 0.13)),  # FP (no label)
    ]
    labels = [
        CalloutLabel("104", (0.20, 0.30, 0.24, 0.33)),
        CalloutLabel("106", (0.55, 0.40, 0.59, 0.43)),
        CalloutLabel("108", (0.70, 0.60, 0.74, 0.63)),  # FN (missed)
    ]
    precision, recall = score_callouts(detected, labels)
    assert precision == Ratio(2, 3)  # 2 TP / (2 TP + 1 FP)
    assert recall == Ratio(2, 3)     # 2 TP / (2 TP + 1 FN)


def test_callout_value_must_match_even_when_box_overlaps():
    detected = [CalloutOccurrence("c1", "104", 0, (0.2, 0.3, 0.24, 0.33))]
    labels = [CalloutLabel("999", (0.2, 0.3, 0.24, 0.33))]  # same box, wrong value
    precision, recall = score_callouts(detected, labels)
    assert precision == Ratio(0, 1) and recall == Ratio(0, 1)
