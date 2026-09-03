"""Native grant line-reference reconstruction (DESIGN.md §12.5)."""

from __future__ import annotations

from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import extract_from_pages
from app.extraction.model import Page, Word
from app.extraction.native import column_boundary, group_lines


def _row(i: int, *, gutter: int | None = None) -> list[Word]:
    cy = 0.1 + 0.05 * i
    y0, y1 = cy - 0.01, cy + 0.01
    words: list[Word] = []
    if gutter is not None:
        words.append(Word(str(gutter), 0.03, y0, 0.06, y1))
    words.append(Word("The", 0.12, y0, 0.18, y1))
    words.append(Word("housing", 0.19, y0, 0.30, y1))
    return words


def _single_column_page() -> Page:
    words: list[Word] = []
    for i in range(15):  # lines 1..15; gutter numbers printed at 5, 10, 15
        words.extend(_row(i, gutter={4: 5, 9: 10, 14: 15}.get(i)))
    return Page(index=0, words=words)


def test_group_lines_groups_by_baseline():
    assert len(group_lines(_single_column_page().words)) == 15


def test_single_column_has_no_boundary():
    assert column_boundary(_single_column_page().words) is None


def test_printed_lines_are_detected_and_interpolated():
    art = extract_from_pages([_single_column_page()], DEFAULT_CONFIG, source_sha256="x")
    assert art.doc_type == "grant" and art.mode == "native"

    located = [(e.locator.column, e.locator.printed_line) for e in art.entries]
    assert located == [(1, n) for n in range(1, 16)]

    # Gutter number excluded from the body text.
    assert art.entries[4].source_text == "The housing"

    methods = {e.locator.printed_line: e.provenance.reference_method for e in art.entries}
    assert methods[5] == "detected" and methods[10] == "detected"
    assert methods[1] == "interpolated" and methods[7] == "interpolated"


def test_boxes_are_valid_normalized():
    art = extract_from_pages([_single_column_page()], DEFAULT_CONFIG, source_sha256="x")
    for e in art.entries:
        x0, y0, x1, y1 = e.box
        assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


def test_reference_numeral_is_not_a_line_number():
    words: list[Word] = []
    for i in range(6):  # gutter anchors on lines 1 and 6
        cy = 0.1 + 0.05 * i
        y0, y1 = cy - 0.01, cy + 0.01
        gutter = {0: 1, 5: 6}.get(i)
        if gutter is not None:
            words.append(Word(str(gutter), 0.03, y0, 0.06, y1))
        if i == 2:
            words.append(Word("104", 0.12, y0, 0.16, y1))
            words.append(Word("housing", 0.17, y0, 0.28, y1))
        else:
            words.append(Word("word", 0.12, y0, 0.20, y1))

    art = extract_from_pages([Page(0, words)], DEFAULT_CONFIG, source_sha256="x")
    third = art.entries[2]
    assert third.locator.printed_line == 3  # interpolated from the fit, not 104
    assert third.source_text.startswith("104")


def test_two_column_split():
    words: list[Word] = []
    for i in range(4):
        cy = 0.1 + 0.05 * i
        y0, y1 = cy - 0.01, cy + 0.01
        words.append(Word("left", 0.10, y0, 0.20, y1))
        words.append(Word("right", 0.60, y0, 0.72, y1))
    page = Page(0, words)
    assert column_boundary(page.words) is not None
    art = extract_from_pages([page], DEFAULT_CONFIG, source_sha256="x")
    columns = {e.locator.column for e in art.entries}
    assert columns == {1, 2}
