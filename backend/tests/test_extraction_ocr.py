"""OCR candidate parsing + routing (DESIGN.md §12.3–12.4)."""

from __future__ import annotations

from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import extract_from_pages
from app.extraction.model import Page, Word
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
    }
    words = words_from_tsv(data, width=1000, height=500, min_confidence=0)
    assert [w.text for w in words] == ["housing", "104"]
    assert words[0].x0 == 0.05 and words[0].y0 == 0.2
    assert words[0].confidence == 95.0


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


def test_ocr_method_recorded_in_provenance():
    words = [
        Word("1", 0.03, 0.09, 0.06, 0.11, confidence=88.0),  # gutter number
        Word("The", 0.12, 0.09, 0.20, 0.11, confidence=91.0),
        Word("housing", 0.21, 0.09, 0.35, 0.11, confidence=80.0),
    ]
    art = extract_from_pages(
        [Page(0, words)], DEFAULT_CONFIG, source_sha256="x", page_methods=["ocr"]
    )
    assert art.mode == "ocr"
    entry = art.entries[0]
    assert entry.provenance.extraction_method == "ocr"
    assert entry.text_confidence == "medium"
    # Average of body-word confidences (The, housing); the gutter '1' is excluded.
    assert abs(entry.provenance.ocr_confidence - 85.5) < 1e-6


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
