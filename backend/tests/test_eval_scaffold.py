"""Corpus label scaffolding from an extraction artifact (DESIGN.md §19.1)."""

from __future__ import annotations

from app.eval.cases import callout_case, grant_reference_case
from app.eval.labels import DocumentLabels
from app.eval.scaffold import build_scaffold


def test_scaffold_prefills_references_from_grant_entries():
    art = grant_reference_case().artifact
    sc = build_scaffold(art, doc_id="US-TEST", doc_type="auto")

    assert sc["doc_id"] == "US-TEST"
    assert sc["doc_type"] == "grant"
    assert sc["spec_page_indices"] == [0]
    assert sc["_scaffold"]["verified"] is False
    assert sc["_scaffold"]["counts"]["references"] == len(art.entries)

    first = sc["references"][0]
    assert first["ordinal"] == 0
    assert first["column"] == 1 and first["printed_line"] == 1
    assert len(first["box"]) == 4 and all(isinstance(v, float) for v in first["box"])


def test_scaffold_is_loadable_and_ignores_metadata():
    """A scaffold already round-trips through the label loader (unknown keys ignored),
    so it scores immediately — reviewing is what makes the score meaningful."""
    art = grant_reference_case().artifact
    sc = build_scaffold(art, doc_id="US-TEST", doc_type="grant")

    labels = DocumentLabels.from_dict(sc)  # must not raise on the _scaffold block
    assert isinstance(labels, DocumentLabels)
    assert labels.doc_type == "grant"
    assert len(labels.references) == len(art.entries)
    assert labels.references[0].column == 1


def test_scaffold_carries_callouts():
    art = callout_case().artifact
    sc = build_scaffold(art, doc_id="DRAW", doc_type="grant")
    values = {c["value"] for c in sc["callouts"]}
    assert {"104", "106", "108"} <= values
    assert all(len(c["box"]) == 4 for c in sc["callouts"])


def test_scaffold_sampling_keeps_ordinals_and_evenly_spaces():
    art = grant_reference_case(n_lines=15).artifact
    sc = build_scaffold(art, doc_id="US-TEST", doc_type="grant", sample=5)
    ordinals = [r["ordinal"] for r in sc["references"]]
    assert len(ordinals) == 5
    assert ordinals == sorted(ordinals)  # still in reading order
    assert ordinals[0] == 0  # first line kept
    assert sc["_scaffold"]["counts"]["references"] == 5


def test_scaffold_application_uses_paragraph_locator():
    from app.extraction.artifact import Artifact, Entry, Provenance
    from app.extraction.locator import ApplicationLocator

    entry = Entry(
        entry_id="l0", ordinal=0, page_index=0,
        locator=ApplicationLocator(paragraph=42),
        box=(0.1, 0.1, 0.9, 0.12), source_text="x", display_text="x",
        provenance=Provenance(extraction_method="native"),
    )
    art = Artifact(
        schema_version=2, source_sha256="s", doc_type="application", page_count=1,
        engine_version="0", config_hash="h", mode="native", disposition="complete",
        entries=[entry],
    )
    sc = build_scaffold(art, doc_id="APP", doc_type="auto")
    assert sc["doc_type"] == "application"
    assert sc["references"][0]["paragraph"] == 42
    assert "column" not in sc["references"][0]
