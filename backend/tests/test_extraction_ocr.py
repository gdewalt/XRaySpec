"""OCR candidate parsing + routing (DESIGN.md §12.3–12.4)."""

from __future__ import annotations

import pytest

from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import extract_from_pages, route_page
from app.extraction.model import Page, Word
from app.extraction.native import reconstruct_line_text
from app.extraction.ocr import (
    _map_crop_words,
    _unrotate_right_angle_words,
    dedupe_words,
    ocr_available,
    words_from_tsv,
)


def test_ocr_available_is_bool():
    # Never raises, even when Tesseract is not installed.
    assert isinstance(ocr_available(), bool)


def test_words_from_tsv_parses_word_level():
    data = {
        "level": [1, 5, 5],  # 1 = block (skipped), 5 = word
        "text": ["", "housing", "104"],
        "conf": [-1, 95.0, 40.0],
        "left": [0, 50, 200],
        "top": [0, 100, 100],
        "width": [0, 80, 30],
        "height": [0, 20, 20],
        "block_num": [0, 2, 2],
        "par_num": [0, 3, 3],
        "line_num": [0, 1, 1],
    }
    words = words_from_tsv(data, width=1000, height=500, min_confidence=0)
    assert [w.text for w in words] == ["housing", "104"]
    assert words[0].x0 == 0.05 and words[0].y0 == 0.2
    assert words[0].confidence == 95.0
    assert (words[0].block_num, words[0].paragraph_num, words[0].line_num) == (2, 3, 1)


def test_words_from_tsv_respects_min_confidence():
    data = {
        "level": [5, 5],
        "text": ["good", "bad"],
        "conf": [90.0, 10.0],
        "left": [0, 0],
        "top": [0, 0],
        "width": [10, 10],
        "height": [10, 10],
    }
    words = words_from_tsv(data, 100, 100, min_confidence=50)
    assert [w.text for w in words] == ["good"]


def test_crop_and_rotation_coordinates_map_back_to_pdf_space():
    cropped = Word("104", 0.0, 0.0, 1.0, 1.0, confidence=90.0)
    mapped = _map_crop_words([cropped], (0.05, 0.10, 0.45, 0.90))[0]
    assert (mapped.x0, mapped.y0, mapped.x1, mapped.y1) == (0.05, 0.10, 0.45, 0.90)

    rotated = Word("104", 0.20, 0.70, 0.30, 0.80, confidence=90.0)
    restored = _unrotate_right_angle_words([rotated], 90)[0]
    assert restored.x0 == pytest.approx(0.20)
    assert restored.y0 == pytest.approx(0.20)
    assert restored.x1 == pytest.approx(0.30)
    assert restored.y1 == pytest.approx(0.30)


def test_orientation_passes_deduplicate_the_same_label_by_confidence():
    words = [
        Word("104", 0.10, 0.20, 0.15, 0.23, confidence=72.0),
        Word("104", 0.101, 0.201, 0.151, 0.231, confidence=94.0),
        Word("106", 0.30, 0.40, 0.35, 0.43, confidence=80.0),
    ]
    result = dedupe_words(words)
    assert [word.text for word in result] == ["104", "106"]
    assert result[0].confidence == 94.0


def test_route_page_uses_separate_column_ocr_for_scanned_specification():
    full_words = [
        Word(f"word{i}", 0.10, 0.10 + (i % 60) * 0.01, 0.20, 0.11 + (i % 60) * 0.01)
        for i in range(121)
    ]
    calls: list[str] = []

    def full_pass(*_args, **_kwargs):
        return full_words

    def column_pass(_pdf, _index, _config, base_words):
        calls.append("columns")
        assert base_words == full_words
        return [Word("enhanced", 0.1, 0.2, 0.2, 0.22)]

    result = route_page(
        b"pdf",
        Page(0, []),
        DEFAULT_CONFIG,
        True,
        ocr_fn=full_pass,
        specification_ocr_fn=column_pass,
    )
    assert calls == ["columns"]
    assert [word.text for word in result.words] == ["enhanced"]


def test_route_page_uses_rotated_drawing_pass_for_scanned_sheet():
    initial = [
        Word("Sheet", 0.1, 0.04, 0.2, 0.06),
        Word("1", 0.21, 0.04, 0.23, 0.06),
        Word("of", 0.24, 0.04, 0.27, 0.06),
        Word("2", 0.28, 0.04, 0.30, 0.06),
    ]
    calls: list[str] = []

    def full_pass(*_args, **_kwargs):
        return initial

    def drawing_pass(*_args, **_kwargs):
        calls.append("drawing")
        return [Word("104", 0.3, 0.4, 0.35, 0.43, confidence=92.0)]

    result = route_page(
        b"pdf",
        Page(0, []),
        DEFAULT_CONFIG,
        True,
        ocr_fn=full_pass,
        drawing_ocr_fn=drawing_pass,
    )
    assert calls == ["drawing"]
    assert result.is_drawing is True
    assert [callout.value for callout in result.callouts] == ["104"]


def test_ocr_line_reconstruction_preserves_large_gaps_as_tabs():
    words = [
        Word("Label", 0.10, 0.10, 0.15, 0.12, confidence=90.0),
        Word("Value", 0.24, 0.10, 0.29, 0.12, confidence=90.0),
        Word("units", 0.30, 0.10, 0.35, 0.12, confidence=90.0),
    ]
    assert reconstruct_line_text(words, preserve_tabs=True) == "Label\t\tValue units"


def test_ocr_method_recorded_in_provenance():
    # A small OCR'd spec page: column header, centre gutter (×5), two columns.
    words = [
        Word("1", 0.28, 0.045, 0.30, 0.062, confidence=95.0),
        Word("2", 0.70, 0.045, 0.72, 0.062, confidence=95.0),
    ]
    for i in range(10):
        cy = 0.11 + 0.025 * i
        y0, y1 = cy - 0.008, cy + 0.008
        conf_a, conf_b = (91.0, 80.0) if i == 0 else (85.0, 85.0)
        words += [
            Word("The", 0.12, y0, 0.20, y1, confidence=conf_a),
            Word("housing", 0.21, y0, 0.35, y1, confidence=conf_b),
            Word("right", 0.55, y0, 0.72, y1, confidence=88.0),
        ]
        if (i + 1) % 5 == 0:  # gutter numbers 5 and 10
            words.append(Word(str(i + 1), 0.49, y0, 0.51, y1, confidence=99.0))

    art = extract_from_pages(
        [Page(0, words)], DEFAULT_CONFIG, source_sha256="x", page_methods=["ocr"]
    )
    assert art.mode == "ocr"
    first = {(e.locator.column, e.locator.printed_line): e for e in art.entries}[(1, 1)]
    assert first.provenance.extraction_method == "ocr"
    assert first.text_confidence == "medium"
    assert first.source_text == "The housing"
    # Body-word confidences (91, 80); the excluded gutter number (99) is not counted.
    assert abs(first.provenance.ocr_confidence - 85.5) < 1e-6
    assert all("5" not in e.source_text for e in art.entries)  # gutter numbers excluded


def test_hybrid_mode_when_pages_mixed():
    native = [Word("alpha", 0.1, 0.1, 0.2, 0.12), Word("beta", 0.3, 0.1, 0.4, 0.12)]
    ocr = [Word("gamma", 0.1, 0.1, 0.2, 0.12, confidence=90.0)]
    art = extract_from_pages(
        [Page(0, native), Page(1, ocr)],
        DEFAULT_CONFIG,
        source_sha256="x",
        page_methods=["native", "ocr"],
    )
    assert art.mode == "hybrid"
    assert art.quality["ocr_pages"] == 1


def test_ocr_paragraph_hierarchy_marks_breaks():
    words = [
        Word("1", 0.28, 0.045, 0.30, 0.062, confidence=95.0),
        Word("2", 0.70, 0.045, 0.72, 0.062, confidence=95.0),
    ]
    for i in range(10):
        cy = 0.11 + 0.025 * i
        paragraph = 1 if i < 5 else 2
        words += [
            Word(
                "line", 0.12, cy - 0.008, 0.20, cy + 0.008,
                confidence=90.0, block_num=1, paragraph_num=paragraph, line_num=i + 1,
            ),
            Word(
                str(i), 0.22, cy - 0.008, 0.25, cy + 0.008,
                confidence=90.0, block_num=1, paragraph_num=paragraph, line_num=i + 1,
            ),
            Word(
                "right", 0.55, cy - 0.008, 0.66, cy + 0.008,
                confidence=90.0, block_num=2, paragraph_num=1, line_num=i + 1,
            ),
        ]
        if (i + 1) % 5 == 0:
            words.append(Word(str(i + 1), 0.49, cy - 0.008, 0.51, cy + 0.008, 99.0))

    art = extract_from_pages(
        [Page(0, words)], DEFAULT_CONFIG, source_sha256="x", page_methods=["ocr"]
    )
    left = [entry for entry in art.entries if entry.locator.column == 1]
    assert left[0].paragraph_start is False
    assert left[5].paragraph_start is True
