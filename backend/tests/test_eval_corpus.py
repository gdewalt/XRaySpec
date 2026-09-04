"""Loading and scoring the real labeled corpus (DESIGN.md §19.1)."""

from __future__ import annotations

import json
from pathlib import Path

from app.eval.cases import grant_reference_case
from app.eval.corpus import extract_resolver, iter_labeled, score_corpus
from app.eval.scaffold import build_scaffold

ART = grant_reference_case().artifact


def _write(dir: Path, doc_id: str, *, verified: bool | None) -> None:
    sc = build_scaffold(ART, doc_id=doc_id, doc_type="grant")
    if verified is None:
        sc.pop("_scaffold")  # hand-written file: no scaffold block
    else:
        sc["_scaffold"]["verified"] = verified
    (dir / f"{doc_id}.json").write_text(json.dumps(sc), encoding="utf-8")


def test_iter_labeled_includes_only_verified(tmp_path):
    _write(tmp_path, "VERIFIED", verified=True)
    _write(tmp_path, "RAW", verified=False)  # unreviewed scaffold — skipped
    _write(tmp_path, "HANDWRITTEN", verified=None)  # no _scaffold — counts
    (tmp_path / "example.json").write_text(json.dumps({"doc_id": "example", "doc_type": "grant"}))

    ids = {doc_id for doc_id, _ in iter_labeled(tmp_path)}
    assert ids == {"VERIFIED", "HANDWRITTEN"}  # RAW skipped, example skipped


def test_score_corpus_scores_resolvable_and_skips_the_rest(tmp_path):
    _write(tmp_path, "US-A", verified=True)
    _write(tmp_path, "US-B", verified=True)

    # Resolver has an artifact for A only; B is skipped, not failed.
    def resolver(doc_id: str, doc_type: str):
        return ART if doc_id == "US-A" else None

    metrics, skipped = score_corpus(resolver, tmp_path)
    assert {m.doc_id for m in metrics} == {"US-A"}  # CaseMetrics.doc_id is the label's doc_id
    assert [d for d, _ in skipped] == ["US-B"]
    # The scored case reproduces the grant references (perfect on its own artifact).
    assert metrics[0].reference_exact.value == 1.0


def test_score_corpus_empty_when_nothing_verified(tmp_path):
    _write(tmp_path, "RAW", verified=False)
    metrics, skipped = score_corpus(lambda *_: ART, tmp_path)
    assert metrics == [] and skipped == []


def test_extract_resolver_skips_missing_pdf(tmp_path):
    resolver = extract_resolver(tmp_path)
    assert resolver("nope", "grant") is None  # no PDF, no extraction attempted
