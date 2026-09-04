"""Golden harness and corpus calibration (DESIGN.md §19, §25.3 #3).

The extraction engine has two *measured* accuracy limits (grant printed-line
precision on center-gutter layouts, and drawing-callout yield) that the design
insists are calibrated against a hand-labeled corpus (§19.1) rather than tuned to
a single example. This package is that measurement apparatus:

- ``labels`` — the ground-truth label schema for a corpus document (page roles,
  reference locators + boxes, callouts, associations), with JSON (de)serialization
  so a corpus grows as data files, not code.
- ``metrics`` — pure scoring of an extraction :class:`Artifact` against labels,
  computing the §19.1 measures (reference exact/±1, box IoU, callout precision/
  recall, association precision, substitution rejection, spec-page precision/recall).
- ``harness`` — run a corpus of cases, aggregate the metrics, and compare them to
  the §19.2 provisional release gates, producing a pass/fail report.
- ``calibrate`` — sweep a config threshold across the corpus and report the metric
  curve, so a threshold is chosen from evidence (the calibration the README defers).

The seed corpus is synthetic (``cases``) so the harness runs in CI without the
PDF/OCR libraries or copyrighted PDFs; real labeled patents drop in as JSON.
"""

from __future__ import annotations

from .labels import (
    AssociationLabel,
    CalloutLabel,
    DocumentLabels,
    ReferenceLabel,
)
from .metrics import CaseMetrics, score_case

__all__ = [
    "AssociationLabel",
    "CalloutLabel",
    "CaseMetrics",
    "DocumentLabels",
    "ReferenceLabel",
    "score_case",
]
