"""Golden-harness CLI (DESIGN.md §19).

Run ``python -m app.eval.run`` to score the seed corpus against the §19.2 gates
and print the ``alignment_min_ratio`` calibration sweep. Exit code is non-zero if
any applicable gate fails, so it can gate CI once the real corpus lands.
"""

from __future__ import annotations

import sys

from .calibrate import calibrate_alignment_min_ratio, recommend_min_ratio, render_sweep
from .cases import seed_corpus
from .harness import aggregate, evaluate_gates, render_report
from .metrics import score_case


def main() -> int:
    cases = seed_corpus()
    metrics = [
        score_case(c.artifact, c.labels, detected_callouts=c.detected_callouts) for c in cases
    ]
    agg = aggregate(metrics)
    gates = evaluate_gates(agg)
    print(render_report(agg, gates))
    print()

    values = [0.60, 0.72, 0.80, 0.85, 0.90, 0.95]
    points = calibrate_alignment_min_ratio(values)
    print(render_sweep(points, recommend_min_ratio(points)))

    failed = [g for g in gates if g.applicable and not g.passed]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
