"""Drawing callout detection + mention association (DESIGN.md §12.7)."""

from __future__ import annotations

from app.extraction.artifact import (
    CalloutOccurrence,
    Entry,
    FigureMention,
    NumeralMention,
    Provenance,
)
from app.extraction.callouts import (
    associate_mentions,
    detect_callouts,
    detect_figure_occurrences,
    detect_page_figure,
    is_drawing_page,
)
from app.extraction.locator import GrantLocator
from app.extraction.model import Word


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
