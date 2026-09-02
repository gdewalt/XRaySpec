"""Portable-save validator/migrator unit tests (DESIGN.md §11.3)."""

from __future__ import annotations

import json

import pytest

from app.imports import ImportLimits, ImportValidationError, analyze_import


def _bytes(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _v2(**over) -> dict:
    doc = {
        "schema_version": 2,
        "title": "Widget",
        "entries": [
            {
                "locator": {"kind": "grant", "column": 3, "printed_line": 15},
                "page_index": 6,
                "source_text": "A housing 104 receives the shaft.",
            }
        ],
    }
    doc.update(over)
    return doc


def test_valid_v2():
    parsed = analyze_import(_bytes(_v2()))
    assert parsed.schema_version == 2
    assert parsed.needs_migration is False
    assert parsed.doc_type == "grant"
    assert parsed.entries[0]["entry_id"] == "line_0000000"
    assert parsed.entries[0]["locator"] == {"kind": "grant", "column": 3, "printed_line": 15}


def test_legacy_v1_is_migrated():
    doc = {
        "schema_version": 1,
        "entries": [{"ref": "3:15", "page_index": 6, "source_text": "line text"}],
    }
    parsed = analyze_import(_bytes(doc))
    assert parsed.needs_migration is True
    assert parsed.entries[0]["locator"] == {"kind": "grant", "column": 3, "printed_line": 15}
    assert parsed.entries[0]["provenance"]["migrated"] is True
    assert any("version-1" in w for w in parsed.warnings)


def test_application_doc_type():
    doc = _v2(
        entries=[
            {
                "locator": {"kind": "application", "paragraph": "0042"},
                "page_index": 2,
                "source_text": "paragraph text",
            }
        ]
    )
    assert analyze_import(_bytes(doc)).doc_type == "application"


def test_file_ids_and_keys_are_ignored():
    doc = _v2(id="art_evil", owner_id="usr_someone", object_key="secret/key")
    doc["entries"][0]["entry_id"] = "line_injected"
    parsed = analyze_import(_bytes(doc))
    # Regenerated, not restored from the file.
    assert parsed.entries[0]["entry_id"] == "line_0000000"
    payload = parsed.payload()
    assert "owner_id" not in payload and "object_key" not in payload


def test_bookmarks_resolve_by_index_and_ref():
    doc = _v2(
        entries=[
            {"locator": {"kind": "grant", "column": 3, "printed_line": 15},
             "page_index": 6, "source_text": "a"},
            {"locator": {"kind": "grant", "column": 3, "printed_line": 16},
             "page_index": 6, "source_text": "b"},
        ],
        bookmarks=[{"entry_index": 1, "label": "second"}, {"ref": "3:15", "label": "first"}],
    )
    parsed = analyze_import(_bytes(doc))
    ids = {b["entry_id"] for b in parsed.bookmarks}
    assert ids == {"line_0000000", "line_0000001"}


def test_unresolved_bookmark_is_dropped_with_warning():
    doc = _v2(bookmarks=[{"ref": "9:99", "label": "nowhere"}])
    parsed = analyze_import(_bytes(doc))
    assert parsed.bookmarks == []
    assert any("bookmark" in w for w in parsed.warnings)


def test_valid_source_hash_kept_bad_dropped():
    good = "a" * 64
    assert analyze_import(_bytes(_v2(source={"sha256": good}))).source_sha256 == good
    assert analyze_import(_bytes(_v2(source={"sha256": "xyz"}))).source_sha256 is None


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"not json", "invalid_json"),
        (b"[1,2,3]", "invalid_json"),
        (None, "unsupported_schema"),  # replaced below
    ],
)
def test_rejects_bad_payloads(payload, reason):
    if payload is None:
        payload = _bytes({"schema_version": 3, "entries": [{"source_text": "x"}]})
    with pytest.raises(ImportValidationError) as e:
        analyze_import(payload)
    assert e.value.reason == reason


def test_rejects_empty_entries():
    with pytest.raises(ImportValidationError) as e:
        analyze_import(_bytes({"schema_version": 2, "entries": []}))
    assert e.value.reason == "empty"


def test_rejects_bad_box():
    doc = _v2()
    doc["entries"][0]["box"] = [0.5, 0.5, 0.4, 0.6]  # x0 >= x1
    with pytest.raises(ImportValidationError) as e:
        analyze_import(_bytes(doc))
    assert e.value.reason == "invalid_box"


def test_rejects_bad_locator():
    doc = _v2()
    doc["entries"][0]["locator"] = {"kind": "grant", "column": 0, "printed_line": 15}
    with pytest.raises(ImportValidationError) as e:
        analyze_import(_bytes(doc))
    assert e.value.reason == "invalid_locator"


def test_enforces_limits():
    with pytest.raises(ImportValidationError) as too_big:
        analyze_import(_bytes(_v2()), ImportLimits(max_bytes=10))
    assert too_big.value.reason == "too_large"

    with pytest.raises(ImportValidationError) as too_many:
        analyze_import(_bytes(_v2()), ImportLimits(max_entries=0))
    assert too_many.value.reason == "too_many_entries"

    deep = {"schema_version": 2, "entries": [{"locator": {"kind": "grant", "column": 1,
            "printed_line": 1}, "page_index": 0, "source_text": "x", "nested": {}}]}
    node = deep["entries"][0]["nested"]
    for _ in range(80):
        node["n"] = {}
        node = node["n"]
    with pytest.raises(ImportValidationError) as too_deep:
        analyze_import(_bytes(deep), ImportLimits(max_depth=16))
    assert too_deep.value.reason == "too_deep"
