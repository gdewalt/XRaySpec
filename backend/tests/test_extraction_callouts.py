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
    has_drawing_sheet_header,
    infer_missing_figure_occurrences,
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


def test_specification_prose_below_masthead_is_not_a_sheet_header():
    words = [
        Word("5", 0.30, 0.09, 0.32, 0.10),
        Word("6", 0.70, 0.09, 0.72, 0.10),
        Word("material", 0.12, 0.13, 0.18, 0.15),
        Word("forming", 0.19, 0.13, 0.24, 0.15),
        Word("the", 0.25, 0.13, 0.28, 0.15),
        Word("carbon", 0.29, 0.13, 0.34, 0.15),
        Word("fiber", 0.35, 0.13, 0.39, 0.15),
        Word("sheet", 0.40, 0.13, 0.44, 0.15),
        Word("of", 0.60, 0.14, 0.62, 0.16),
    ]

    assert has_drawing_sheet_header(words) is False


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


def test_exact_subfigure_label_does_not_also_become_a_numeric_confusion():
    words = [
        Word("Figure", 0.10, 0.30, 0.18, 0.33),
        Word("14i.", 0.19, 0.30, 0.24, 0.33),
    ]

    figures = detect_figure_occurrences(words, page_index=32)

    assert [figure.figure_id for figure in figures] == ["14I"]


def test_fuzzy_figure_prefixes_are_recovered_on_drawing_sheets():
    words = [
        Word("F1G.", 0.10, 0.30, 0.16, 0.33, confidence=88.0),
        Word("I4", 0.17, 0.30, 0.21, 0.33, confidence=90.0),
        Word("A", 0.22, 0.30, 0.24, 0.33, confidence=89.0),
        Word("Fiqure", 0.55, 0.60, 0.63, 0.63, confidence=86.0),
        Word("7", 0.64, 0.60, 0.66, 0.63, confidence=92.0),
    ]
    figures = detect_figure_occurrences(words, page_index=2)

    assert {(figure.figure_id, figure.method) for figure in figures} == {
        ("14A", "fuzzy_prefix"),
        ("7", "fuzzy_prefix"),
    }


def test_widely_spaced_drafting_figure_labels_are_recovered():
    labels = ["FIlG..1", "FIlG.2", "FIG.3", "FlG.4", "FIlG.S", "FlG.6", "FIG.7"]
    figures = []
    for page_index, label in enumerate(labels, start=1):
        figures.extend(
            detect_figure_occurrences(
                [Word(label, 0.40, 0.75, 0.58, 0.79, confidence=55.0)],
                page_index=page_index,
            )
        )

    assert [(figure.figure_id, figure.page_index) for figure in figures] == [
        (str(figure_id), figure_id) for figure_id in range(1, 8)
    ]


def test_adjacent_caption_word_is_not_attached_as_a_subfigure_suffix():
    figures = detect_figure_occurrences(
        [Word("FIGURE", 0.2, 0.3, 0.3, 0.34), Word("2PRIOR", 0.31, 0.3, 0.42, 0.34)],
        page_index=3,
    )

    assert [figure.figure_id for figure in figures] == ["2"]


def test_page_order_penalizes_an_unsupported_figure_outlier():
    figures = [
        FigureOccurrence("1", 1, (0.1, 0.2, 0.2, 0.3), confidence=95.0),
        FigureOccurrence("99", 2, (0.1, 0.2, 0.2, 0.3), confidence=99.0),
        FigureOccurrence("2", 3, (0.1, 0.2, 0.2, 0.3), confidence=95.0),
    ]

    filtered = filter_figure_occurrences(figures, {"1", "2"})

    assert [figure.figure_id for figure in filtered] == ["1", "2"]
    assert all(figure.detection_score == 1.0 for figure in filtered)


def test_missing_sheet_is_inferred_only_when_neighbors_and_specification_agree():
    detected = [
        FigureOccurrence(
            "14A", 10, (0.1, 0.2, 0.2, 0.3), detection_score=0.94,
            method="spec_exact",
        ),
        FigureOccurrence(
            "14C", 12, (0.1, 0.2, 0.2, 0.3), detection_score=0.91,
            method="spec_exact",
        ),
    ]

    inferred = infer_missing_figure_occurrences(
        detected, [10, 11, 12], {"14A", "14B", "14C"}
    )
    figure_b = next(figure for figure in inferred if figure.figure_id == "14B")
    assert figure_b.page_index == 11
    assert figure_b.method == "sequence_inferred"
    assert figure_b.detection_score == 0.592

    not_inferred = infer_missing_figure_occurrences(
        detected, [10, 11, 12], {"14A", "14C"}
    )
    assert [figure.figure_id for figure in not_inferred] == ["14A", "14C"]


def test_multiple_figure_groups_are_distributed_across_blank_sheets():
    detected = [
        FigureOccurrence(
            "8", 8, (0.1, 0.2, 0.2, 0.3), detection_score=0.94, method="spec_exact"
        ),
        FigureOccurrence(
            "12A", 11, (0.1, 0.2, 0.2, 0.3), detection_score=0.91, method="spec_exact"
        ),
    ]

    inferred = infer_missing_figure_occurrences(
        detected,
        [8, 9, 10, 11],
        {"8", "9", "9A", "9B", "10", "11", "12A"},
    )
    pages = {figure.figure_id: figure.page_index for figure in inferred}

    assert pages == {
        "8": 8,
        "9A": 9,
        "9B": 9,
        "10": 10,
        "11": 10,
        "12A": 11,
    }


def test_low_confidence_caption_box_is_sequence_corrected_instead_of_discarded():
    raw = [
        FigureOccurrence("10", 10, (0.1, 0.2, 0.2, 0.3), confidence=95.0),
        # The FIGURE 11 caption is spatially correct but its thin digits were
        # read as 20 by drawing OCR.
        FigureOccurrence("20", 10, (0.5, 0.6, 0.6, 0.7), confidence=0.0),
        FigureOccurrence("12A", 11, (0.1, 0.2, 0.2, 0.3), confidence=95.0),
    ]

    filtered = filter_figure_occurrences(raw, {"10", "11", "12A"})
    recovered = next(figure for figure in filtered if figure.figure_id == "11")

    assert recovered.page_index == 10
    assert recovered.box == (0.5, 0.6, 0.6, 0.7)
    assert recovered.method == "spec_sequence_corrected"


def test_two_weak_boxes_fill_two_consecutive_sheet_gaps_by_ocr_similarity():
    raw = [
        *[
            FigureOccurrence(
                str(value), 19, (value / 100, 0.2, value / 100 + 0.02, 0.3), confidence=95.0
            )
            for value in range(29, 35)
        ],
        FigureOccurrence("95", 19, (0.5, 0.5, 0.6, 0.6), confidence=0.0),
        FigureOccurrence("7", 19, (0.7, 0.2, 0.8, 0.3), confidence=48.0),
        FigureOccurrence("37", 20, (0.1, 0.2, 0.2, 0.3), confidence=95.0),
    ]

    filtered = filter_figure_occurrences(raw, {str(value) for value in range(29, 38)})
    recovered = {
        figure.figure_id: figure
        for figure in filtered
        if figure.figure_id in {"35", "36"}
    }

    assert recovered["35"].box == (0.5, 0.5, 0.6, 0.6)
    assert recovered["36"].box == (0.7, 0.2, 0.8, 0.3)


def test_internal_multifigure_hole_is_assigned_to_that_sheet():
    detected = [
        FigureOccurrence(
            str(value),
            20,
            (0.1, 0.2, 0.2, 0.3),
            detection_score=0.9,
            method="spec_exact",
        )
        for value in (37, 39, 40, 41)
    ]

    inferred = infer_missing_figure_occurrences(
        detected, [20], {str(value) for value in range(37, 42)}
    )

    assert {figure.figure_id: figure.page_index for figure in inferred} == {
        str(value): 20 for value in range(37, 42)
    }


def test_interleaved_figure_spills_to_the_blank_sheet_after_its_group():
    detected = [
        *[
            FigureOccurrence(
                str(value),
                22,
                (0.1, 0.2, 0.2, 0.3),
                detection_score=0.9,
                method="spec_exact",
            )
            for value in (50, 52, 53, 54, 55, 56)
        ],
        FigureOccurrence("57", 24, (0.1, 0.2, 0.2, 0.3), detection_score=0.9, method="spec_exact"),
    ]

    inferred = infer_missing_figure_occurrences(
        detected, [22, 23, 24], {str(value) for value in range(50, 58)}
    )
    figure_51 = next(figure for figure in inferred if figure.figure_id == "51")

    assert figure_51.page_index == 23
    assert figure_51.method == "sequence_inferred"


def test_sequence_keeps_small_sheet_inversion_and_drops_distant_duplicate():
    figures = [
        FigureOccurrence("2", 4, (0.1, 0.2, 0.2, 0.3), confidence=0.0),
        FigureOccurrence("4", 4, (0.3, 0.2, 0.4, 0.3), confidence=0.0),
        FigureOccurrence("3", 5, (0.1, 0.2, 0.2, 0.3), confidence=0.0),
        FigureOccurrence("5", 5, (0.3, 0.2, 0.4, 0.3), confidence=0.0),
        FigureOccurrence("5", 20, (0.1, 0.2, 0.2, 0.3), confidence=95.0),
    ]

    filtered = filter_figure_occurrences(figures, {"2", "3", "4", "5"})

    assert {(figure.figure_id, figure.page_index) for figure in filtered} == {
        ("2", 4),
        ("4", 4),
        ("3", 5),
        ("5", 5),
    }


def test_first_sheet_duplicate_of_figure_two_is_corrected_to_figure_one():
    figures = [
        FigureOccurrence("2", 3, (0.1, 0.2, 0.2, 0.3), confidence=2.0),
        FigureOccurrence("2", 4, (0.1, 0.2, 0.2, 0.3), confidence=80.0),
        FigureOccurrence("3", 5, (0.1, 0.2, 0.2, 0.3), confidence=80.0),
    ]

    filtered = filter_figure_occurrences(figures, {"1", "2", "3"})

    assert {(figure.figure_id, figure.page_index) for figure in filtered} == {
        ("1", 3),
        ("2", 4),
        ("3", 5),
    }


def test_leading_gap_spans_populated_sheet_and_following_blank_sheet():
    detected = [
        FigureOccurrence("49", 21, (0.1, 0.2, 0.2, 0.3), detection_score=0.9, method="spec_exact"),
        *[
            FigureOccurrence(
                str(value),
                22,
                (0.1, 0.2, 0.2, 0.3),
                detection_score=0.9,
                method="spec_exact",
            )
            for value in (52, 53, 54, 55, 56)
        ],
        FigureOccurrence("57", 24, (0.1, 0.2, 0.2, 0.3), detection_score=0.9, method="spec_exact"),
    ]

    inferred = infer_missing_figure_occurrences(
        detected, [21, 22, 23, 24], {str(value) for value in range(49, 58)}
    )
    pages = {figure.figure_id: figure.page_index for figure in inferred}

    assert pages["50"] == 22
    assert pages["51"] == 23


def test_short_gap_does_not_invent_base_figure_when_subfigures_exist():
    detected = [
        FigureOccurrence("10", 10, (0.1, 0.2, 0.2, 0.3), detection_score=0.9, method="spec_exact"),
        FigureOccurrence("12A", 11, (0.1, 0.2, 0.2, 0.3), detection_score=0.9, method="spec_exact"),
    ]

    inferred = infer_missing_figure_occurrences(
        detected, [10, 11], {"10", "11", "12", "12A"}
    )
    pages = {figure.figure_id: figure.page_index for figure in inferred}

    assert pages["11"] == 10
    assert "12" not in pages


def test_multiple_missing_captions_before_next_sheet_stay_on_dense_sheet():
    detected = [
        *[
            FigureOccurrence(
                str(value),
                19,
                (0.1, 0.2, 0.2, 0.3),
                detection_score=0.9,
                method="spec_exact",
            )
            for value in range(29, 35)
        ],
        FigureOccurrence("37", 20, (0.1, 0.2, 0.2, 0.3), detection_score=0.9, method="spec_exact"),
    ]

    inferred = infer_missing_figure_occurrences(
        detected, [19, 20], {str(value) for value in range(29, 38)}
    )
    pages = {figure.figure_id: figure.page_index for figure in inferred}

    assert pages["35"] == 19
    assert pages["36"] == 19


def test_sequence_recovery_does_not_create_base_figure_for_subfigures():
    figures = [
        FigureOccurrence("8", 8, (0.1, 0.2, 0.2, 0.3), confidence=90.0),
        FigureOccurrence("0E", 9, (0.1, 0.2, 0.2, 0.3), confidence=12.0),
        FigureOccurrence("10", 10, (0.1, 0.2, 0.2, 0.3), confidence=90.0),
    ]

    filtered = filter_figure_occurrences(figures, {"8", "9", "9A", "9B", "10"})
    completed = infer_missing_figure_occurrences(
        filtered, [8, 9, 10], {"8", "9", "9A", "9B", "10"}
    )
    by_id = {figure.figure_id: figure for figure in completed}

    assert "9" not in by_id
    assert by_id["9A"].page_index == 9
    assert by_id["9B"].page_index == 9


def test_sequence_recovery_ignores_fragments_inside_detected_labels():
    figures = [
        FigureOccurrence("57A", 24, (0.13, 0.68, 0.23, 0.84), confidence=92.0),
        FigureOccurrence("7", 24, (0.14, 0.69, 0.18, 0.83), confidence=4.0),
        FigureOccurrence("0E", 24, (0.43, 0.71, 0.51, 0.86), confidence=5.0),
        FigureOccurrence("58", 24, (0.3, 0.2, 0.4, 0.3), confidence=92.0),
        FigureOccurrence("60", 24, (0.6, 0.2, 0.7, 0.3), confidence=92.0),
        FigureOccurrence("61", 24, (0.7, 0.2, 0.8, 0.3), confidence=92.0),
        FigureOccurrence("62", 25, (0.1, 0.2, 0.2, 0.3), confidence=92.0),
    ]

    filtered = filter_figure_occurrences(
        figures, {"57", "58", "59", "60", "61", "62"}
    )
    figure_59 = next(figure for figure in filtered if figure.figure_id == "59")

    assert figure_59.box == (0.43, 0.71, 0.51, 0.86)
    assert figure_59.method == "spec_sequence_corrected"


def test_sequence_recovery_can_repair_confident_impossible_identifier():
    figures = [
        FigureOccurrence("37", 20, (0.1, 0.7, 0.2, 0.8), confidence=92.0),
        FigureOccurrence("80A", 20, (0.3, 0.1, 0.5, 0.2), confidence=74.0),
        FigureOccurrence("39", 20, (0.6, 0.1, 0.7, 0.2), confidence=92.0),
        FigureOccurrence("40", 20, (0.4, 0.7, 0.5, 0.8), confidence=92.0),
        FigureOccurrence("41", 20, (0.7, 0.7, 0.8, 0.8), confidence=92.0),
        FigureOccurrence("42", 21, (0.1, 0.2, 0.2, 0.3), confidence=92.0),
    ]

    filtered = filter_figure_occurrences(
        figures, {str(value) for value in range(37, 43)}
    )
    figure_38 = next(figure for figure in filtered if figure.figure_id == "38")

    assert figure_38.box == (0.3, 0.1, 0.5, 0.2)
    assert figure_38.method == "spec_sequence_corrected"


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
