"""Golden harness + calibration over the seed corpus (DESIGN.md §19.1-19.2)."""

from __future__ import annotations

from pathlib import Path

from app.eval.calibrate import (
    calibrate_alignment_min_ratio,
    recommend_min_ratio,
)
from app.eval.cases import seed_corpus
from app.eval.harness import aggregate, evaluate_gates, render_report
from app.eval.labels import DocumentLabels, load_labels_file
from app.eval.metrics import score_case


def _run_seed():
    cases = seed_corpus()
    metrics = [
        score_case(c.artifact, c.labels, detected_callouts=c.detected_callouts) for c in cases
    ]
    return aggregate(metrics)


def test_seed_corpus_passes_all_applicable_gates():
    agg = _run_seed()
    gates = evaluate_gates(agg)
    failed = [g for g in gates if g.applicable and not g.passed]
    assert failed == [], render_report(agg, gates)


def test_seed_corpus_measures_are_sane():
    agg = _run_seed()
    assert agg.reference_exact.value == 1.0
    assert agg.callout_recall.value == 1.0
    assert agg.substitution_rejection.value == 1.0  # wrong-doc: nothing substituted
    assert agg.silent_verified_on_ambiguous == 0  # ambiguous never silently verified


def test_report_renders_pass_and_na_rows():
    agg = _run_seed()
    text = render_report(agg, evaluate_gates(agg))
    assert "ALL GATES PASS" in text
    assert "callout recall" in text


def test_calibration_curve_trades_coverage_for_rejection():
    points = calibrate_alignment_min_ratio([0.60, 0.72, 0.80, 0.85, 0.90, 0.95])
    by_ratio = {p.min_ratio: p for p in points}
    # Low thresholds cover everything but fail to reject a near-duplicate wrong doc.
    assert by_ratio[0.72].coverage == 1.0 and by_ratio[0.72].rejection == 0.0
    # Full rejection is only reached at the high end, and it costs coverage.
    assert by_ratio[0.95].rejection == 1.0
    assert by_ratio[0.95].coverage < 1.0
    # Rejection is monotonic non-decreasing in the threshold.
    rej = [by_ratio[r].rejection for r in sorted(by_ratio)]
    assert rej == sorted(rej)


def test_recommend_lowest_threshold_that_fully_rejects():
    points = calibrate_alignment_min_ratio([0.60, 0.72, 0.80, 0.85, 0.90, 0.95])
    assert recommend_min_ratio(points) == 0.95


def test_recommend_none_when_nothing_rejects():
    points = calibrate_alignment_min_ratio([0.10, 0.20])
    assert recommend_min_ratio(points) is None


def test_example_label_file_round_trips():
    path = Path(__file__).resolve().parents[1] / "corpus" / "example.json"
    labels = load_labels_file(path)
    assert isinstance(labels, DocumentLabels)
    assert labels.doc_type == "grant"
    assert labels.references[0].column == 1
    assert labels.callouts[0].value == "104"
    assert labels.associations[0].status == "verified"
