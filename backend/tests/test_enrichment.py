"""Clean-text alignment, identity verification, provider parsing (DESIGN.md §13)."""

from __future__ import annotations

from dataclasses import replace

from app.enrichment import (
    align_entries,
    enrich_from_page_html,
    enrich_from_ppubs_html,
    extract_ppubs_text,
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


GOOGLE_PARAGRAPH_HTML = """
<html><head>
<meta content="US7840427B2" name="citation_patent_number">
<meta content="Shared transport system" name="DC.title">
<meta name="DC.contributor" scheme="inventor" content="Ada Example">
<meta name="DC.contributor" scheme="assignee" content="Transit Labs">
<meta name="citation_filing_date" content="2007-03-01">
</head><body>
<section itemprop="abstract"><div class="abstract">A concise &amp; useful abstract.</div></section>
<section itemprop="description">
  <div class="description-paragraph">First line. Continued line.</div>
  <div class="description-paragraph">Second paragraph begins.</div>
</section>
</body></html>
"""


def test_google_text_preserves_paragraphs_abstract_and_front_page_metadata():
    provider = extract_provider_text(GOOGLE_PARAGRAPH_HTML)
    assert provider.canonical == "US7840427B2"
    assert provider.description.splitlines() == [
        "First line. Continued line.",
        "Second paragraph begins.",
    ]
    assert provider.abstract == "A concise & useful abstract."
    assert provider.metadata == (
        ("Inventor", "Ada Example"),
        ("Original assignee", "Transit Labs"),
        ("Filing date", "2007-03-01"),
    )


def test_google_alignment_uses_google_paragraphs_and_discards_layout_guesses():
    entries = [
        _entry("First line.", 1),
        replace(
            _entry("Continued line.", 2),
            paragraph_start=True,
            paragraph_source="layout",
        ),
        _entry("Second paragraph begins.", 3),
    ]
    aligned, identity = enrich_from_page_html(
        entries,
        GOOGLE_PARAGRAPH_HTML,
        "US7840427B2",
        DEFAULT_CONFIG,
    )
    assert identity.status == "verified"
    assert aligned[0].paragraph_start is False
    assert aligned[1].paragraph_start is False
    assert aligned[1].paragraph_source is None
    assert aligned[2].paragraph_start is True
    assert aligned[2].paragraph_source == "google_patents"


PPUBS_HTML = """
<html><head><title>US-7840427-B2 - Patent Public Search | USPTO</title></head><body>
<h2>Shared transport system and service network</h2>
<section><h3>Abstract</h3><p>A shared transport network.</p></section>
<section><h3>Background/Summary</h3>
<p>(1) FIELD OF THE INVENTION<br>(2) The houslng 104 receives the shaft 108.</p></section>
<section><h3>Description</h3>
<p>(3) The shaft rotates freely.<br>continued without a marker.<br>
(4) A final paragraph follows.</p><p>still the same numbered paragraph.</p></section>
<section><h3>Claims</h3>
<p>1. A transport method.<br>2. The method of claim 1.</p></section>
</body></html>
"""


def test_extract_ppubs_text_removes_markers_and_preserves_paragraphs():
    provider = extract_ppubs_text(PPUBS_HTML)
    assert provider.canonical == "US7840427B2"
    assert provider.title == "Shared transport system and service network"
    assert provider.abstract == "A shared transport network."
    assert "(1)" not in provider.clean_text and "(4)" not in provider.clean_text
    assert provider.description.splitlines() == [
        "FIELD OF THE INVENTION",
        "The houslng 104 receives the shaft 108.",
        "The shaft rotates freely. continued without a marker.",
        "A final paragraph follows. still the same numbered paragraph.",
    ]
    assert provider.claims == "1. A transport method. 2. The method of claim 1."


def test_ppubs_alignment_uses_clean_text_and_paragraph_boundaries():
    entries = [
        _entry("The houslng 104 receives the shaft 108.", 1),
        _entry("The shaft rotates freely.", 2),
        replace(
            _entry("continued without a marker.", 3),
            paragraph_start=True,
            paragraph_source="layout",
        ),
    ]
    aligned, identity = enrich_from_ppubs_html(
        entries, PPUBS_HTML, "US7840427B2", DEFAULT_CONFIG
    )
    assert identity.status == "verified"
    assert aligned[0].provenance.provider == "uspto_ppubs"
    assert aligned[0].paragraph_start is True
    assert aligned[0].paragraph_source == "uspto_numbered"
    assert aligned[1].paragraph_start is True
    assert aligned[1].paragraph_source == "uspto_numbered"
    # This line is still inside USPTO paragraph (3), so its layout guess must
    # not create the kind of false extra spacing seen between grant lines.
    assert aligned[2].paragraph_start is False
    assert aligned[2].paragraph_source is None
