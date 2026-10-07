"""Text-side figure-reference and reference-numeral detection (DESIGN.md §12.6-12.7)."""

from __future__ import annotations

from app.extraction.artifact import Entry, Provenance
from app.extraction.figures import detect_figure_references, detect_reference_numerals
from app.extraction.locator import GrantLocator


def _entry(text: str, entry_id: str = "line_0000001") -> Entry:
    return Entry(
        entry_id=entry_id,
        ordinal=1,
        page_index=0,
        locator=GrantLocator(column=1, printed_line=1),
        box=(0.1, 0.1, 0.9, 0.12),
        source_text=text,
        display_text=text,
        provenance=Provenance(extraction_method="native"),
    )


def _fig_ids(text: str) -> list[list[str]]:
    return [m.figure_ids for m in detect_figure_references([_entry(text)])]


def test_single_figure_reference():
    assert _fig_ids("As shown in FIG. 3, the device") == [["3"]]
    assert _fig_ids("As shown in Figure 7, the device") == [["7"]]
    assert _fig_ids("See Fig 8A for another view") == [["8A"]]


def test_figure_range_numeric():
    assert _fig_ids("FIGS. 4-6 depict") == [["4", "5", "6"]]


def test_figure_list_and_alnum():
    assert _fig_ids("see FIGS. 1, 2 and 3") == [["1", "2", "3"]]
    assert _fig_ids("FIG. 12A illustrates") == [["12A"]]
    assert _fig_ids("FIGS. 3A-3C show") == [["3A", "3B", "3C"]]


def test_spaced_subfigure_references_are_normalized():
    assert _fig_ids("FIG. 14 A illustrates the first state") == [["14A"]]
    assert _fig_ids("FIGS. 14A and 14 B show the states") == [["14A", "14B"]]
    assert _fig_ids("FIG . 14 A illustrates the first state") == [["14A"]]


def test_figure_span_points_at_reference():
    m = detect_figure_references([_entry("text FIG. 5 here")])[0]
    assert m.raw_text.upper().startswith("FIG")
    assert "FIG. 5" in "text FIG. 5 here"[m.span[0] : m.span[1]]


def _numerals(text: str):
    return [(m.value, m.component_label) for m in detect_reference_numerals([_entry(text)])]


def test_simple_reference_numeral():
    assert _numerals("the housing 104 receives the shaft 108") == [
        ("104", "housing"),
        ("108", "shaft"),
    ]


def test_coordinated_numerals_share_label():
    assert _numerals("the housings 104 and 106 are shown") == [
        ("104", "housings"),
        ("106", "housings"),
    ]


def test_alphanumeric_reference_numeral():
    assert _numerals("the bracket 104A holds") == [("104A", "bracket")]


def test_excludes_figure_numbers_and_claims():
    assert _numerals("see FIG. 104 and claim 12") == []


def test_excludes_years_and_measurements():
    assert _numerals("filed in 2024 by the applicant") == []
    assert _numerals("a width 104 mm across") == []


def test_reference_numeral_reference_pattern():
    # "reference numeral 104" — the marker word triggers detection.
    assert ("104", "numeral") in _numerals("indicated by reference numeral 104")
