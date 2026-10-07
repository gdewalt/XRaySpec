"""Drawing callout detection + mention association (DESIGN.md §12.7)."""

from __future__ import annotations

from app.extraction.artifact import (
    CalloutOccurrence,
    Entry,
    FigureMention,
    FigureOccurrence,
    NumeralMention,
    Provenance,
)
from app.extraction.callouts import (
    assign_callouts_to_figures,
    associate_mentions,
    detect_callouts,
    detect_figure_occurrences,
    detect_page_figure,
    filter_callouts_by_values,
    filter_figure_occurrences,
    is_drawing_page,
    specification_callout_values,
)
from app.extraction.locator import GrantLocator
from app.extraction.model import Page, Word


def _w(text: str, x: float = 0.4) -> Word:
    return Word(text, x, 0.4, x + 0.04, 0.42, confidence=90.0)


def _entry(entry_id: str, text: str, ordinal: int) -> Entry:
    return Entry(
        entry_id=entry_id,
        ordinal=ordinal,
        page_index=0,
        locator=GrantLocator(column=1, printed_line=ordinal),
        box=(0.1, 0.1, 0.9, 0.12),
        source_text=text,
        display_text=text,
        provenance=Provenance(extraction_method="native"),
    )


def test_is_drawing_page():
    assert is_drawing_page([_w("104"), _w("108"), _w("FIG"), _w("3"), _w("32")]) is True
    assert is_drawing_page([]) is True  # image page, no OCR text
    prose = [_w("the"), _w("device"), _w("comprises"), _w("a"), _w("housing"), _w("104")]
    assert is_drawing_page(prose) is False  # mostly words


def test_detect_page_figure():
    assert detect_page_figure([_w("FIG."), _w("3")]) == "3"
    assert detect_page_figure([_w("FIG."), _w("3"), _w("FIG."), _w("4")]) is None
    assert detect_page_figure([_w("104"), _w("108")]) is None


def test_detect_callouts():
    words = [_w("104"), _w("FIG"), _w("3"), _w("108"), _w("housing"), _w("104A")]
    callouts = detect_callouts(words, page_index=3, figure_id="12A")
    assert [c.value for c in callouts] == ["104", "108", "104A"]  # 1-digit "3" and words excluded
    assert all(c.figure_id == "12A" and c.page_index == 3 for c in callouts)


def test_figure_identifier_is_not_duplicated_as_a_component_callout():
    words = [
        Word("FIG.", 0.10, 0.30, 0.16, 0.33, confidence=91.0),
        Word("14B", 0.17, 0.30, 0.22, 0.33, confidence=92.0),
        Word("104", 0.55, 0.60, 0.60, 0.63, confidence=90.0),
    ]
    figures = detect_figure_occurrences(words, page_index=2)
    callouts = detect_callouts(words, 2, None, figure_occurrences=figures)
    assert [callout.value for callout in callouts] == ["104"]


def test_sheet_header_numbers_are_not_drawing_callouts():
    words = [
        Word("Sheet", 0.40, 0.03, 0.46, 0.05),
        Word("12", 0.47, 0.03, 0.50, 0.05),
        Word("of", 0.51, 0.03, 0.54, 0.05),
        Word("24", 0.55, 0.03, 0.58, 0.05),
        Word("104", 0.40, 0.40, 0.45, 0.42),
    ]
    assert [callout.value for callout in detect_callouts(words, 3, "1")] == ["104"]


def test_entire_drawing_header_band_is_ignored():
    words = [
        Word("US", 0.05, 0.06, 0.09, 0.08),
        Word("7840427", 0.10, 0.06, 0.18, 0.08),
        Word("Sheet", 0.40, 0.03, 0.46, 0.05),
        Word("12", 0.47, 0.03, 0.50, 0.05),
        Word("of", 0.51, 0.03, 0.54, 0.05),
        Word("24", 0.55, 0.03, 0.58, 0.05),
        Word("104", 0.40, 0.40, 0.45, 0.42),
    ]
    assert [callout.value for callout in detect_callouts(words, 3, "1")] == ["104"]


def test_specification_callouts_are_bold_numbers_in_column_body():
    page = Page(
        0,
        [
            Word("104", 0.15, 0.30, 0.19, 0.32, is_bold=True),
            Word("108", 0.20, 0.30, 0.24, 0.32, is_bold=False),
            Word("20", 0.49, 0.30, 0.51, 0.32, is_bold=True),  # centre line number
            Word("2024", 0.70, 0.30, 0.75, 0.32, is_bold=True),  # year
            Word("112", 0.70, 0.04, 0.74, 0.06, is_bold=True),  # running header
            Word("104A", 0.70, 0.40, 0.76, 0.42, is_bold=True),
        ],
    )
    assert specification_callout_values([page], fallback_values=["999"]) == {
        "104",
        "104A",
        "999",
    }


def test_ocr_specification_uses_vetted_text_mentions_as_fallback():
    page = Page(0, [Word("housing", 0.10, 0.30, 0.20, 0.32)])
    assert specification_callout_values([page], fallback_values=["104", "2024"]) == {"104"}


def test_drawing_callouts_are_scored_against_specification_values():
    callouts = [
        CalloutOccurrence("a", "104", 1, (0.1, 0.2, 0.2, 0.3), confidence=55.0),
        CalloutOccurrence("b", "108", 1, (0.3, 0.2, 0.4, 0.3), confidence=70.0),
    ]
    ranked = filter_callouts_by_values(callouts, {"104"})
    assert [item.value for item in ranked] == ["104"]
    assert ranked[0].method == "spec_exact"
    assert ranked[0].detection_score is not None


def test_high_confidence_drawing_only_callout_survives_and_confusable_value_matches_spec():
    callouts = [
        CalloutOccurrence("a", "I04", 1, (0.1, 0.2, 0.2, 0.3), confidence=72.0),
        CalloutOccurrence("b", "118", 1, (0.3, 0.2, 0.4, 0.3), confidence=95.0),
    ]
    ranked = filter_callouts_by_values(callouts, {"104"})
    assert [(item.value, item.method) for item in ranked] == [
        ("104", "spec_fuzzy"),
        ("118", "drawing_only"),
    ]


def test_unique_one_edit_spec_match_recovers_damaged_low_confidence_callout():
    callouts = [
        CalloutOccurrence("a", "�204}", 1, (0.1, 0.2, 0.2, 0.3), confidence=8.0),
    ]
    ranked = filter_callouts_by_values(callouts, {"1204": 1.0, "1207": 1.0})
    assert len(ranked) == 1
    assert ranked[0].value == "1204"
    assert ranked[0].method == "spec_fuzzy"
    assert ranked[0].detection_score == 0.348


def test_one_edit_spec_match_must_be_unambiguous():
    callouts = [
        CalloutOccurrence("a", "1209", 1, (0.1, 0.2, 0.2, 0.3), confidence=40.0),
    ]
    assert filter_callouts_by_values(callouts, {"1201": 1.0, "1202": 1.0}) == []


def test_same_length_damage_is_not_silently_mapped_to_the_wrong_spec_value():
    callouts = [
        CalloutOccurrence("a", "L304", 1, (0.1, 0.2, 0.2, 0.3), confidence=20.0),
    ]
    assert filter_callouts_by_values(callouts, {"1204": 1.0, "1205": 1.0}) == []


def test_sheet_header_identifies_a_drawing_page_without_becoming_a_callout():
    words = [
        Word("Sheet", 0.40, 0.03, 0.46, 0.05),
        Word("2", 0.47, 0.03, 0.49, 0.05),
        Word("of", 0.50, 0.03, 0.53, 0.05),
        Word("8", 0.54, 0.03, 0.56, 0.05),
        *[_w(f"label{i}") for i in range(130)],
    ]
    assert is_drawing_page(words) is True
    assert detect_callouts(words, 1, None) == []


def test_figure_and_fig_labels_are_detected_on_drawing_sheets():
    words = [
        Word("Sheet", 0.40, 0.03, 0.46, 0.05),
        Word("3", 0.47, 0.03, 0.49, 0.05),
        Word("of", 0.50, 0.03, 0.53, 0.05),
        Word("8", 0.54, 0.03, 0.56, 0.05),
        Word("Figure", 0.10, 0.30, 0.18, 0.33),
        Word("7", 0.19, 0.30, 0.21, 0.33),
        Word("Fig.", 0.55, 0.60, 0.61, 0.63),
        Word("8A", 0.62, 0.60, 0.66, 0.63),
    ]
    figures = detect_figure_occurrences(words, page_index=2)
    assert [(figure.figure_id, figure.page_index) for figure in figures] == [
        ("7", 2),
        ("8A", 2),
    ]


def test_spaced_and_ocr_confused_subfigure_labels_are_detected():
    words = [
        Word("FIG.", 0.10, 0.30, 0.16, 0.33, confidence=91.0),
        Word("I4", 0.17, 0.30, 0.21, 0.33, confidence=88.0),
        Word("A", 0.22, 0.30, 0.24, 0.33, confidence=86.0),
        Word("Fig.", 0.55, 0.60, 0.61, 0.63, confidence=90.0),
        Word("14b", 0.62, 0.60, 0.67, 0.63, confidence=93.0),
    ]
    figures = detect_figure_occurrences(words, page_index=2)
    assert {figure.figure_id for figure in figures} >= {"14A", "14B"}


def test_drawing_figures_prefer_specification_ids_without_hard_filtering_clear_labels():
    figures = [
        FigureOccurrence("14A", 2, (0.1, 0.2, 0.2, 0.3), confidence=80.0),
        FigureOccurrence("14A", 2, (0.2, 0.2, 0.3, 0.3), confidence=95.0),
        FigureOccurrence("15", 3, (0.1, 0.2, 0.2, 0.3), confidence=99.0),
    ]
    filtered = filter_figure_occurrences(figures, {"14A"})
    assert len(filtered) == 2
    assert filtered[0].figure_id == "14A"
    assert filtered[0].confidence == 95.0
    assert filtered[0].method == "spec_exact"
    assert filtered[1].figure_id == "15"
    assert filtered[1].method == "drawing_only"


def test_exact_specification_figure_can_rescue_zero_confidence_tiled_ocr():
    figures = [
        FigureOccurrence("12A", 18, (0.15, 0.22, 0.42, 0.25), confidence=0.0),
    ]
    filtered = filter_figure_occurrences(figures, {"12A"})
    assert len(filtered) == 1
    assert filtered[0].method == "spec_exact"
    assert filtered[0].detection_score == 0.38


def test_callouts_are_assigned_to_nearest_supported_subfigure():
    figures = [
        FigureOccurrence("14A", 2, (0.10, 0.10, 0.20, 0.15)),
        FigureOccurrence("14B", 2, (0.70, 0.70, 0.80, 0.75)),
    ]
    callouts = [
        CalloutOccurrence("a", "104", 2, (0.15, 0.20, 0.18, 0.23)),
        CalloutOccurrence("b", "108", 2, (0.74, 0.60, 0.77, 0.63)),
    ]
    assigned = assign_callouts_to_figures(callouts, figures)
    assert [callout.figure_id for callout in assigned] == ["14A", "14B"]


def _numeral(value: str, entry_id: str) -> NumeralMention:
    return NumeralMention(entry_id=entry_id, value=value, component_label="housing", span=(4, 7))


def test_associate_verified_in_figure_context():
    entries = [_entry("line_0000001", "housing 104 in FIG. 12A", 1)]
    figs = [FigureMention("line_0000001", "FIG. 12A", (15, 23), ["12A"])]
    callouts = [CalloutOccurrence("callout_a", "104", 3, (0.5, 0.5, 0.6, 0.52), figure_id="12A")]
    assoc = associate_mentions([_numeral("104", "line_0000001")], callouts, figs, entries)
    assert assoc[0].status == "verified"
    assert assoc[0].selected_callout_ids == ["callout_a"]


def test_associate_probable_without_context():
    entries = [_entry("line_0000001", "housing 104", 1)]
    callouts = [CalloutOccurrence("callout_a", "104", 3, (0.5, 0.5, 0.6, 0.52), figure_id="7")]
    assoc = associate_mentions([_numeral("104", "line_0000001")], callouts, [], entries)
    assert assoc[0].status == "probable"
    assert assoc[0].selected_callout_ids == ["callout_a"]


def test_associate_ambiguous():
    entries = [_entry("line_0000001", "housing 104", 1)]
    callouts = [
        CalloutOccurrence("callout_a", "104", 3, (0.1, 0.1, 0.2, 0.12), figure_id="3"),
        CalloutOccurrence("callout_b", "104", 4, (0.3, 0.3, 0.4, 0.32), figure_id="7"),
    ]
    assoc = associate_mentions([_numeral("104", "line_0000001")], callouts, [], entries)
    assert assoc[0].status == "ambiguous"
    assert set(assoc[0].candidate_callout_ids) == {"callout_a", "callout_b"}
    assert assoc[0].selected_callout_ids == []


def test_associate_unresolved():
    entries = [_entry("line_0000001", "housing 999", 1)]
    callouts = [CalloutOccurrence("callout_a", "104", 3, (0.5, 0.5, 0.6, 0.52), figure_id="3")]
    assoc = associate_mentions([_numeral("999", "line_0000001")], callouts, [], entries)
    assert assoc[0].status == "unresolved"
    assert assoc[0].selected_callout_ids == []


def test_context_narrows_ambiguity_to_verified():
    # Two "104" callouts, but the mention's figure context picks one.
    entries = [_entry("line_0000001", "housing 104 shown in FIG. 3", 1)]
    figs = [FigureMention("line_0000001", "FIG. 3", (21, 27), ["3"])]
    callouts = [
        CalloutOccurrence("callout_a", "104", 3, (0.1, 0.1, 0.2, 0.12), figure_id="3"),
        CalloutOccurrence("callout_b", "104", 4, (0.3, 0.3, 0.4, 0.32), figure_id="7"),
    ]
    assoc = associate_mentions([_numeral("104", "line_0000001")], callouts, figs, entries)
    assert assoc[0].status == "verified"
    assert assoc[0].selected_callout_ids == ["callout_a"]


def test_detects_each_figure_on_multi_figure_sheet():
    words = [
        Word("FIG.", 0.10, 0.10, 0.16, 0.12, confidence=92.0),
        Word("3", 0.17, 0.10, 0.19, 0.12, confidence=94.0),
        Word("FIG.", 0.58, 0.10, 0.64, 0.12, confidence=91.0),
        Word("4A", 0.65, 0.10, 0.69, 0.12, confidence=93.0),
    ]
    figures = detect_figure_occurrences(words, page_index=7)
    assert [(figure.figure_id, figure.page_index) for figure in figures] == [("3", 7), ("4A", 7)]
    assert figures[0].box[2] < figures[1].box[0]
