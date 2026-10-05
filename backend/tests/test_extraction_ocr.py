"""OCR candidate parsing + routing (DESIGN.md §12.3–12.4)."""

from __future__ import annotations

from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import extract_from_pages
from app.extraction.model import Page, Word
from app.extraction.native import reconstruct_line_text
from app.extraction.ocr import ocr_available, words_from_tsv


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
