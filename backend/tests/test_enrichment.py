"""Clean-text alignment, identity verification, provider parsing (DESIGN.md §13)."""

from __future__ import annotations

from dataclasses import replace

from app.enrichment import (
    align_entries,
    enrich_from_page_html,
    enrich_from_ppubs_html,
    extract_ppubs_text,
    extract_provider_text,
    repair_serialized_display_overlaps,
    strip_leading_line_overlap,
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


def test_alignment_drops_clipped_repeats_at_column_line_boundaries():
    clean = (
        "It is an object of the invention to provide a system that enables regular "
        "highway traffic and privately owned personal transport systems to augment "
        "and enable public mass transit networks."
    )
    entries = [
        _entry("It is an object of the invention to provide a system that enables", 1),
        _entry("nables regular highway traffic and privately owned personal transport", 2),
        _entry("ansport systems to augment and enable public mass transit networks.", 3),
    ]

    aligned = align_entries(entries, clean, DEFAULT_CONFIG, identity_verified=True)

    assert aligned[0].display_text.endswith("that enables")
    assert aligned[1].display_text == (
        "regular highway traffic and privately owned personal transport"
    )
    assert aligned[2].display_text == (
        "systems to augment and enable public mass transit networks."
    )
    assert aligned[1].source_text.startswith("nables ")
    assert aligned[2].source_text.startswith("ansport ")


def test_alignment_drops_short_page_edge_fragment_without_dropping_real_word():
    clean = (
        "A Driver Software Interface determines if the Driver 13 is complying with "
        "the pick-up request. The surface face remains visible."
    )
    entries = [
        _entry("A Driver Software Inter", 1),
        _entry("ace determines if the Driver 13 is complying with the pick-up request.", 2),
        _entry("The surface", 3),
        _entry("face remains visible.", 4),
    ]

    aligned = align_entries(entries, clean, DEFAULT_CONFIG, identity_verified=True)

    assert aligned[0].display_text == "A Driver Software Interface"
    assert aligned[1].display_text == (
        "determines if the Driver 13 is complying with the pick-up request."
    )
    assert aligned[2].display_text == "The surface"
    assert aligned[3].display_text == "face remains visible."


def test_alignment_reconciles_numeric_short_and_interior_boundary_fragments():
    clean = (
        "12 Marlboro St to Albany Airport; via an Internet web interface or dedicated "
        "kiosk. The Destination Point could also be specified by category or purpose."
    )
    entries = [
        _entry("12", 1),
        _entry("2 Marlboro St to Albany Airport; via an Internet web interface", 2),
        _entry("iterface or dedicated kiosk.", 3),
        _entry("The Destination Point could also be specified by category or", 4),
        _entry("r purpose.", 5),
    ]

    aligned = align_entries(entries, clean, DEFAULT_CONFIG, identity_verified=True)

    assert aligned[0].display_text == "12"
    assert aligned[1].display_text == (
        "Marlboro St to Albany Airport; via an Internet web interface"
    )
    assert aligned[2].display_text == "or dedicated kiosk."
    assert aligned[3].display_text == (
        "The Destination Point could also be specified by category or"
    )
    assert aligned[4].display_text == "purpose."


def test_overlap_repair_is_conservative_and_supports_legacy_artifacts():
    assert strip_leading_line_overlap("that enables", "nables regular traffic") == (
        "regular traffic"
    )
    assert strip_leading_line_overlap("network matches", "1atches the supply") == (
        "the supply"
    )
    assert strip_leading_line_overlap("mass transit networks.", "etworks. It follows") == (
        "It follows"
    )
    assert strip_leading_line_overlap("public transport", "transport remains available") == (
        "transport remains available"
    )
    assert strip_leading_line_overlap(
        "A Driver Software Interface",
        "ace determines if the Driver complies",
        allow_short=True,
    ) == "determines if the Driver complies"
    assert strip_leading_line_overlap(
        "an Internet web interface",
        "iterface or dedicated kiosk",
        allow_short=True,
    ) == "or dedicated kiosk"
    assert (
        strip_leading_line_overlap(
            "specified by category or", "r purpose", allow_short=True
        )
        == "purpose"
    )
    assert strip_leading_line_overlap("address 12", "2 Marlboro St", allow_short=True) == (
        "Marlboro St"
    )
    assert strip_leading_line_overlap("the surface", "face remains visible") == (
        "face remains visible"
    )

    entries = [
        {
            "entry_id": "line_1",
            "ordinal": 1,
            "page_index": 4,
            "locator": {"kind": "grant", "column": 2, "printed_line": 54},
            "display_text": "a system that enables",
        },
        {
            "entry_id": "line_2",
            "ordinal": 2,
            "page_index": 4,
            "locator": {"kind": "grant", "column": 2, "printed_line": 55},
            "display_text": "nables regular highway traffic",
        },
    ]
    repaired = repair_serialized_display_overlaps(entries)
    assert repaired[1]["display_text"] == "regular highway traffic"
    assert entries[1]["display_text"] == "nables regular highway traffic"

    short_fragment_entries = [
        {
            "entry_id": "line_3",
            "ordinal": 3,
            "page_index": 36,
            "locator": {"kind": "grant", "column": 8, "printed_line": 65},
            "display_text": "A Driver Software Interface",
            "provenance": {"alignment_method": "fuzzy"},
        },
        {
            "entry_id": "line_4",
            "ordinal": 4,
            "page_index": 36,
            "locator": {"kind": "grant", "column": 8, "printed_line": 66},
            "display_text": "ace determines if the Driver complies",
            "provenance": {"alignment_method": "unmatched"},
        },
    ]
    repaired_short = repair_serialized_display_overlaps(short_fragment_entries)
    assert repaired_short[1]["display_text"] == "determines if the Driver complies"

    for previous, current, expected in [
        ("an Internet web interface", "iterface or dedicated kiosk", "or dedicated kiosk"),
        ("specified by category or", "r purpose", "purpose"),
        ("address 12", "2 Marlboro St", "Marlboro St"),
    ]:
        repaired_boundary = repair_serialized_display_overlaps(
            [
                {
                    "ordinal": 10,
                    "page_index": 36,
                    "locator": {"kind": "grant", "column": 8, "printed_line": 40},
                    "display_text": previous,
                    "provenance": {"alignment_method": "fuzzy"},
                },
                {
                    "ordinal": 11,
                    "page_index": 36,
                    "locator": {"kind": "grant", "column": 8, "printed_line": 41},
                    "display_text": current,
                    "provenance": {"alignment_method": "unmatched"},
                },
            ]
        )
        assert repaired_boundary[1]["display_text"] == expected


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
