"""Clean-text alignment, identity verification, provider parsing (DESIGN.md §13)."""

from __future__ import annotations

from app.enrichment import (
    align_entries,
    enrich_from_page_html,
    extract_provider_text,
    verify_identity,
)
from app.extraction.artifact import Entry, Provenance
from app.extraction.config import DEFAULT_CONFIG
from app.extraction.locator import GrantLocator

CLEAN = "The housing 104 receives the shaft 108. The shaft rotates freely."


def _entry(text: str, ordinal: int, method: str = "ocr") -> Entry:
    return Entry(
        entry_id=f"line_{ordinal:07d}",
        ordinal=ordinal,
        page_index=0,
        locator=GrantLocator(column=1, printed_line=ordinal),
        box=(0.1, 0.1, 0.9, 0.12),
        source_text=text,
        display_text=text,
        provenance=Provenance(extraction_method=method, ocr_confidence=80.0),
    )


def test_alignment_corrects_ocr_and_preserves_source():
    entries = [
        _entry("The houslng 104 receives the shaft 108.", 1),  # OCR error: houslng
        _entry("The shaft rotates freely.", 2),
        _entry("This line is not in the clean text at all.", 3),
    ]
    aligned = align_entries(entries, CLEAN, DEFAULT_CONFIG, identity_verified=True)

    # Line 1: display corrected to clean text; source untouched.
    assert "housing 104" in aligned[0].display_text
    assert aligned[0].source_text == "The houslng 104 receives the shaft 108."
    assert aligned[0].provenance.alignment_method in ("exact", "fuzzy")
    assert aligned[0].provenance.provider == "google_patents"
    assert aligned[0].provenance.identity_verified is True

    # Line 2: clean exact match.
    assert aligned[1].provenance.alignment_method == "exact"

    # Line 3: not in the clean text -> substitution rejected, source kept.
    assert aligned[2].provenance.alignment_method == "unmatched"
    assert aligned[2].display_text == aligned[2].source_text


def test_identity_verification():
    assert verify_identity("US12262260B2", "US12262260B2").status == "verified"
    # Provider gives "US:12262260" (no kind); source has the kind suffix — still verified.
    assert verify_identity("US12262260B2", "US:12262260").status == "verified"
    assert verify_identity("US12262260B2", "US9999999B2").status == "mismatch"
    by_title = verify_identity(
        None, None, source_title="Wireless method", provider_title="Wireless method"
    )
    assert by_title.status == "probable"
    assert verify_identity(None, None).status == "insufficient"


HTML = """
<html><head>
<meta name="DC.title" content="Wireless communication method">
<meta name="citation_patent_number" content="US12262260B2">
</head><body>
<section itemprop="description"><p>The housing 104 receives the shaft.</p></section>
<section itemprop="claims"><div>1. A method comprising receiving a preamble.</div></section>
</body></html>
"""


def test_extract_provider_text():
    pt = extract_provider_text(HTML)
    assert pt.canonical == "US12262260B2"
    assert pt.title == "Wireless communication method"
    assert "housing 104" in pt.description
    assert "method comprising" in pt.claims
    assert "housing 104" in pt.clean_text and "method comprising" in pt.clean_text


def test_enrich_from_page_html_verified():
    entries = [_entry("The houslng 104 receives the shaft.", 1)]
    aligned, identity = enrich_from_page_html(entries, HTML, "US12262260B2", DEFAULT_CONFIG)
    assert identity.status == "verified"
    assert "housing 104" in aligned[0].display_text  # aligned


def test_enrich_blocks_on_identity_mismatch():
    entries = [_entry("The houslng 104 receives the shaft.", 1)]
    aligned, identity = enrich_from_page_html(entries, HTML, "US9999999B2", DEFAULT_CONFIG)
    assert identity.status == "mismatch"
    assert aligned[0].display_text == entries[0].display_text  # unchanged, no alignment
