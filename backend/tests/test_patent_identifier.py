"""Structured patent identifier parsing (DESIGN.md §7.2)."""

from __future__ import annotations

import pytest

from app.patents import PatentParseError, parse_patent_identifier


def test_grant_human_form():
    ident = parse_patent_identifier("US 12,262,260 B2")
    assert ident.doc_type == "grant"
    assert ident.number == "12262260"
    assert ident.kind == "B2"
    assert ident.canonical == "US12262260B2"
    assert ident.display == "US 12,262,260 B2"


def test_grant_canonical_form():
    assert parse_patent_identifier("US12262260B2").canonical == "US12262260B2"


def test_grant_bare_number_no_kind():
    ident = parse_patent_identifier("12,262,260")
    assert ident.doc_type == "grant"
    assert ident.kind is None
    assert ident.canonical == "US12262260"
    assert ident.display == "US 12,262,260"


def test_grant_b1_and_pre2001_a():
    assert parse_patent_identifier("US 9,876,543 B1").kind == "B1"
    a = parse_patent_identifier("6000000A")
    assert a.doc_type == "grant"
    assert a.kind == "A"
    assert a.canonical == "US6000000A"


def test_application_human_form():
    ident = parse_patent_identifier("US 2024/0123456 A1")
    assert ident.doc_type == "application"
    assert ident.number == "20240123456"
    assert ident.kind == "A1"
    assert ident.canonical == "US20240123456A1"
    assert ident.display == "US 2024/0123456 A1"


def test_application_canonical_and_slash_default_kind():
    assert parse_patent_identifier("US20240123456A1").doc_type == "application"
    # A slash implies an application even with no explicit kind.
    inferred = parse_patent_identifier("2024/0123456")
    assert inferred.doc_type == "application"
    assert inferred.kind == "A1"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("banana", "unrecognized"),
        ("US 12345 S", "unsupported_kind"),  # design patent kind
        ("US12345P", "unsupported_kind"),  # plant patent kind
        ("US 2024/012 A1", "invalid_number"),  # application serial too short
        ("20240123456", "ambiguous"),  # 11 digits, no kind, no slash
    ],
)
def test_rejects_with_reason(raw, reason):
    with pytest.raises(PatentParseError) as excinfo:
        parse_patent_identifier(raw)
    assert excinfo.value.reason == reason
