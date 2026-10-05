"""Native grant line-reference reconstruction (DESIGN.md §12.5).

Fixtures mirror the real page: a two-column specification with the printed column
numbers at the top, and line numbers in the **centre gutter** as multiples of five
every ~5th row (both columns share that one numbering by y-row).
"""

from __future__ import annotations

from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import extract_from_pages
from app.extraction.model import Page, Word
from app.extraction.native import column_boundary, group_lines


def _spec_page(
    n_rows: int = 30,
    *,
    left_num: int = 1,
    right_num: int = 2,
    page_index: int = 0,
    include_column_header: bool = True,
    include_running_header: bool = False,
) -> Page:
    """A two-column spec page: column-number header + centre-gutter numbers (×5)."""
    words: list[Word] = []
    if include_column_header:
        words += [
            Word(str(left_num), 0.28, 0.045, 0.30, 0.062),  # printed column numbers
            Word(str(right_num), 0.70, 0.045, 0.72, 0.062),
        ]
    if include_running_header:
        words += [
            Word("US", 0.35, 0.066, 0.39, 0.082),
            Word("7,840,427", 0.40, 0.066, 0.52, 0.082),
            Word("B2", 0.53, 0.066, 0.57, 0.082),
        ]
    for i in range(n_rows):
        cy = 0.11 + 0.025 * i
        y0, y1 = cy - 0.008, cy + 0.008
        words += [
            Word("left", 0.12, y0, 0.30, y1),
            Word("body", 0.32, y0, 0.44, y1),
            Word("right", 0.55, y0, 0.72, y1),
            Word("side", 0.74, y0, 0.88, y1),
        ]
        if (i + 1) % 5 == 0:  # printed gutter number every 5th row (a multiple of 5)
            words.append(Word(str(i + 1), 0.49, y0, 0.51, y1))
    return Page(index=page_index, words=words)


def _flat_rows() -> list[Word]:
    words: list[Word] = []
    for i in range(15):
        cy = 0.1 + 0.05 * i
        words += [Word("The", 0.12, cy - 0.01, 0.18, cy + 0.01),
                  Word("housing", 0.19, cy - 0.01, 0.30, cy + 0.01)]
    return words


def test_group_lines_groups_by_baseline():
    assert len(group_lines(_flat_rows())) == 15


def test_single_column_has_no_boundary():
    assert column_boundary(_flat_rows()) is None


def _by_loc(art) -> dict[tuple[int, int], object]:
    return {(e.locator.column, e.locator.printed_line): e for e in art.entries}


def test_printed_lines_detected_and_interpolated():
    art = extract_from_pages([_spec_page(30)], DEFAULT_CONFIG, source_sha256="x")
    assert art.doc_type == "grant" and art.mode == "native"
    loc = _by_loc(art)

    # Both columns are numbered 1..30 off the shared centre gutter.
    for col in (1, 2):
        assert (col, 1) in loc and (col, 30) in loc

    # The gutter number is excluded from body text.
    assert loc[(1, 5)].source_text == "left body"
    assert loc[(2, 5)].source_text == "right side"

    # Rows on a printed gutter number are "detected"; the rest are interpolated.
    assert loc[(1, 5)].provenance.reference_method == "detected"
    assert loc[(1, 10)].provenance.reference_method == "detected"
    assert loc[(1, 1)].provenance.reference_method == "interpolated"
    assert loc[(1, 7)].provenance.reference_method == "interpolated"


def test_boxes_are_valid_normalized():
    art = extract_from_pages([_spec_page(30)], DEFAULT_CONFIG, source_sha256="x")
    assert art.entries
    for e in art.entries:
        x0, y0, x1, y1 = e.box
        assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


def test_reference_numeral_is_not_a_line_number():
    page = _spec_page(15)
    # Inject a component numeral into a left-column body row (5th row -> line 5).
    row5_cy = 0.11 + 0.025 * 4
    page.words.append(Word("104", 0.12, row5_cy - 0.008, 0.16, row5_cy + 0.008))

    art = extract_from_pages([page], DEFAULT_CONFIG, source_sha256="x")
    line5 = _by_loc(art)[(1, 5)]
    assert line5.locator.printed_line == 5  # from the gutter map, never 104
    assert "104" in line5.source_text  # the numeral stays in the body text


def test_two_column_split():
    art = extract_from_pages([_spec_page(30)], DEFAULT_CONFIG, source_sha256="x")
    assert {e.locator.column for e in art.entries} == {1, 2}
    left = [e for e in art.entries if e.locator.column == 1]
    right = [e for e in art.entries if e.locator.column == 2]
    assert all("right" not in e.source_text for e in left)
    assert all("left" not in e.source_text for e in right)


def test_column_numbers_read_from_the_page():
    # A later spec page prints columns 7 and 8; those numbers must be used.
    art = extract_from_pages(
        [_spec_page(20, left_num=7, right_num=8)], DEFAULT_CONFIG, source_sha256="x"
    )
    assert {e.locator.column for e in art.entries} == {7, 8}


def test_missing_later_header_continues_columns_and_drops_us_running_header():
    pages = [
        _spec_page(30, left_num=7, right_num=8, page_index=3),
        _spec_page(
            30,
            page_index=4,
            include_column_header=False,
            include_running_header=True,
        ),
    ]
    art = extract_from_pages(pages, DEFAULT_CONFIG, source_sha256="x")
    first_columns = {entry.locator.column for entry in art.entries if entry.page_index == 3}
    second_columns = {entry.locator.column for entry in art.entries if entry.page_index == 4}

    assert first_columns == {7, 8}
    assert second_columns == {9, 10}
    assert all("US" not in entry.source_text for entry in art.entries)


def test_stale_detected_column_pair_cannot_restart_a_grant():
    pages = [
        _spec_page(20, left_num=7, right_num=8, page_index=3),
        _spec_page(20, left_num=1, right_num=2, page_index=4),
    ]
    art = extract_from_pages(pages, DEFAULT_CONFIG, source_sha256="x")
    second_columns = {entry.locator.column for entry in art.entries if entry.page_index == 4}

    assert second_columns == {9, 10}


def test_non_spec_page_yields_nothing():
    # A cover/front-matter page: no centre gutter, no column-number pair.
    words = [
        Word("United", 0.30, 0.05, 0.42, 0.07),
        Word("States", 0.44, 0.05, 0.55, 0.07),
        Word("Patent", 0.57, 0.05, 0.68, 0.07),
        Word("Abstract", 0.12, 0.20, 0.25, 0.22),
    ]
    art = extract_from_pages([Page(0, words)], DEFAULT_CONFIG, source_sha256="x")
    assert art.entries == []
