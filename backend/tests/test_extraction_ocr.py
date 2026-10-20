"""OCR candidate parsing + routing (DESIGN.md §12.3–12.4)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PIL import Image

import app.extraction.ocr as ocr_module
from app.extraction.callouts import detect_figure_occurrences
from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import extract_from_pages, route_page
from app.extraction.model import Page, Word
from app.extraction.native import reconstruct_line_text
from app.extraction.ocr import (
    _map_crop_words,
    _unrotate_right_angle_words,
    dedupe_words,
    fuse_drawing_words,
    ocr_available,
    ocr_drawing_words,
    ocr_quality,
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


def test_ocr_quality_tracks_confidence_and_coverage():
    quality = ocr_quality(
        [
            Word("clear", 0.1, 0.1, 0.2, 0.2, confidence=90.0),
            Word("faint", 0.2, 0.1, 0.3, 0.2, confidence=30.0),
        ]
    )
    assert quality.word_count == 2
    assert quality.mean_confidence == 60.0
    assert quality.low_confidence_fraction == 0.5
    assert quality.useful_characters == 10


def test_adaptive_ocr_skips_retries_for_a_strong_first_pass(monkeypatch):
    calls: list[tuple[int, int]] = []
    strong = [Word(f"word{i}", 0.1, 0.1, 0.2, 0.2, confidence=92.0) for i in range(12)]

    def recognize(_image, _config, *, psm, thresholding_method):
        calls.append((psm, thresholding_method))
        return strong

    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)
    result = ocr_module._adaptive_tesseract_words(
        Image.new("L", (20, 20), 255),
        DEFAULT_CONFIG,
        primary_psm=3,
        retry_psm=4,
        minimum_words=12,
    )
    assert result == strong
    assert calls == [(3, 2)]


def test_adaptive_ocr_retries_weak_text_and_keeps_better_candidate(monkeypatch):
    calls: list[tuple[int, int]] = []
    weak = [Word("nolse", 0.1, 0.1, 0.2, 0.2, confidence=22.0)]
    strong = [Word(f"word{i}", 0.1, 0.1, 0.2, 0.2, confidence=94.0) for i in range(12)]

    def recognize(_image, _config, *, psm, thresholding_method):
        calls.append((psm, thresholding_method))
        return weak if thresholding_method == 2 else strong

    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)
    result = ocr_module._adaptive_tesseract_words(
        Image.new("L", (20, 20), 255),
        DEFAULT_CONFIG,
        primary_psm=3,
        retry_psm=4,
        minimum_words=12,
    )
    assert result == strong
    assert calls == [(3, 2), (4, 1)]


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

    clockwise = Word("FIG.", 0.70, 0.20, 0.80, 0.30, confidence=90.0)
    clockwise_restored = _unrotate_right_angle_words([clockwise], -90)[0]
    assert clockwise_restored.x0 == pytest.approx(0.20)
    assert clockwise_restored.y0 == pytest.approx(0.20)
    assert clockwise_restored.x1 == pytest.approx(0.30)
    assert clockwise_restored.y1 == pytest.approx(0.30)


def test_default_drawing_ocr_stops_after_credible_upright_label(monkeypatch):
    image = Image.new("L", (100, 120), 255)
    sizes: list[tuple[int, int]] = []
    monkeypatch.setattr(ocr_module, "render_page", lambda *_args: image)
    monkeypatch.setattr(ocr_module, "preprocess_image", lambda value, _config: (value, 0.0))

    def recognize(
        candidate,
        _config,
        *,
        psm,
        thresholding_method,
        char_whitelist=None,
    ):
        del psm, thresholding_method, char_whitelist
        sizes.append(candidate.size)
        return [Word("FIG.1", 0.1, 0.1, 0.2, 0.15, confidence=92.0)]

    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)
    ocr_drawing_words(b"pdf", 0, DEFAULT_CONFIG)

    assert DEFAULT_CONFIG.ocr_drawing_rotations == (0, -90)
    assert sizes == [(50, 60), (38, 45)]


def test_drawing_ocr_tries_clockwise_correction_after_upright_miss(monkeypatch):
    image = Image.new("L", (100, 120), 255)
    sizes: list[tuple[int, int]] = []
    monkeypatch.setattr(ocr_module, "render_page", lambda *_args: image)
    monkeypatch.setattr(ocr_module, "preprocess_image", lambda value, _config: (value, 0.0))

    def recognize(
        candidate,
        _config,
        *,
        psm,
        thresholding_method,
        char_whitelist=None,
    ):
        del psm, thresholding_method, char_whitelist
        sizes.append(candidate.size)
        if candidate.width > candidate.height:
            return [Word("FIG.1", 0.1, 0.1, 0.2, 0.15, confidence=92.0)]
        return []

    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)
    words = ocr_drawing_words(b"pdf", 0, DEFAULT_CONFIG)

    assert any(word.text == "FIG.1" for word in words)
    assert sizes == [(50, 60), (38, 45), (60, 50), (45, 38)]


def test_drawing_ocr_fuses_complementary_caption_scales(monkeypatch):
    image = Image.new("L", (100, 120), 255)
    monkeypatch.setattr(ocr_module, "render_page", lambda *_args: image)
    monkeypatch.setattr(ocr_module, "preprocess_image", lambda value, _config: (value, 0.0))

    def recognize(
        candidate,
        _config,
        *,
        psm,
        thresholding_method,
        char_whitelist=None,
    ):
        del psm, thresholding_method, char_whitelist
        figure_id = "37" if candidate.width == 50 else "38"
        return [Word(f"Figure{figure_id}", 0.1, 0.1, 0.3, 0.15, confidence=75.0)]

    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)

    words = ocr_drawing_words(
        b"pdf", 0, replace(DEFAULT_CONFIG, ocr_drawing_rotations=(0,))
    )

    assert {figure.figure_id for figure in detect_figure_occurrences(words, 0)} == {
        "37",
        "38",
    }


def test_orientation_passes_deduplicate_the_same_label_by_confidence():
    words = [
        Word("104", 0.10, 0.20, 0.15, 0.23, confidence=72.0),
        Word("104", 0.101, 0.201, 0.151, 0.231, confidence=94.0),
        Word("106", 0.30, 0.40, 0.35, 0.43, confidence=80.0),
    ]
    result = dedupe_words(words)
    assert [word.text for word in result] == ["104", "106"]
    assert result[0].confidence == 94.0


def test_drawing_fusion_rewards_agreement_between_specialized_passes():
    words = [
        Word("104", 0.10, 0.20, 0.15, 0.23, confidence=82.0),
        Word("104", 0.101, 0.201, 0.151, 0.231, confidence=90.0),
    ]
    result = fuse_drawing_words(words)
    assert len(result) == 1
    assert result[0].confidence == 92.0


def test_drawing_ocr_runs_separate_figure_and_callout_profiles(monkeypatch):
    image = Image.new("L", (100, 120), 255)
    calls: list[tuple[int, str | None]] = []

    monkeypatch.setattr(ocr_module, "render_page", lambda *_args: image)
    monkeypatch.setattr(ocr_module, "preprocess_image", lambda value, _config: (value, 0.0))

    def recognize(
        _image,
        _config,
        *,
        psm,
        thresholding_method,
        char_whitelist=None,
    ):
        del thresholding_method
        calls.append((psm, char_whitelist))
        if char_whitelist and "." in char_whitelist:
            return [Word("FIG.1", 0.1, 0.1, 0.2, 0.15, confidence=92.0)]
        return [Word("104", 0.4, 0.4, 0.5, 0.45, confidence=94.0)]

    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)
    config = replace(
        DEFAULT_CONFIG,
        ocr_drawing_rotations=(0,),
        callout_detection_enabled=True,
    )
    words = ocr_drawing_words(b"pdf", 0, config)

    assert [word.text for word in words] == ["FIG.1", "104"]
    assert [psm for psm, _ in calls] == [
        config.ocr_drawing_figure_psm,
        config.ocr_drawing_figure_psm,
        config.ocr_drawing_callout_psm,
    ]
    assert "." in (calls[0][1] or "")
    assert "." not in (calls[2][1] or "")


def test_drawing_ocr_falls_back_to_local_tiles_when_full_page_misses_figure(monkeypatch):
    image = Image.new("L", (100, 120), 255)
    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(ocr_module, "render_page", lambda *_args: image)
    monkeypatch.setattr(ocr_module, "preprocess_image", lambda value, _config: (value, 0.0))

    def recognize(
        _image,
        _config,
        *,
        psm,
        thresholding_method,
        char_whitelist=None,
    ):
        del thresholding_method, char_whitelist
        calls.append((psm, _image.size))
        if _image.height < 35:
            return [Word("Fig12a", 0.1, 0.1, 0.3, 0.2, confidence=70.0)]
        return [Word("104", 0.4, 0.4, 0.5, 0.45, confidence=90.0)]

    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)
    config = DEFAULT_CONFIG
    words = ocr_drawing_words(b"pdf", 0, config)

    assert {figure.figure_id for figure in detect_figure_occurrences(words, 0)} == {"12A"}
    assert calls[:4] == [
        (config.ocr_drawing_figure_psm, (50, 60)),
        (config.ocr_drawing_figure_psm, (38, 45)),
        (config.ocr_drawing_figure_psm, (60, 50)),
        (config.ocr_drawing_figure_psm, (45, 38)),
    ]
    assert sum(
        psm == config.ocr_drawing_figure_psm and height < 35
        for psm, (_width, height) in calls
    ) == 4


def test_drawing_ocr_prefers_low_confidence_caption_over_unrelated_noise(monkeypatch):
    image = Image.new("L", (100, 120), 255)
    monkeypatch.setattr(ocr_module, "render_page", lambda *_args: image)
    monkeypatch.setattr(ocr_module, "preprocess_image", lambda value, _config: (value, 0.0))

    def recognize(
        candidate,
        _config,
        *,
        psm,
        thresholding_method,
        char_whitelist=None,
    ):
        del psm, thresholding_method, char_whitelist
        if candidate.width > candidate.height:
            return [
                Word("Figure", 0.10, 0.10, 0.20, 0.15, confidence=0.0),
                Word("14b.", 0.21, 0.10, 0.28, 0.15, confidence=0.0),
            ]
        return [Word("diagram", 0.4, 0.4, 0.5, 0.45, confidence=99.0)]

    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)

    words = ocr_drawing_words(b"pdf", 0, DEFAULT_CONFIG)

    assert {figure.figure_id for figure in detect_figure_occurrences(words, 0)} == {"14B"}


def test_drawing_ocr_skips_callout_profile_while_identification_is_disabled(monkeypatch):
    image = Image.new("L", (100, 120), 255)
    calls: list[int] = []
    monkeypatch.setattr(ocr_module, "render_page", lambda *_args: image)
    monkeypatch.setattr(ocr_module, "preprocess_image", lambda value, _config: (value, 0.0))

    def recognize(
        _image,
        _config,
        *,
        psm,
        thresholding_method,
        char_whitelist=None,
    ):
        del thresholding_method, char_whitelist
        calls.append(psm)
        return [
            Word("Figure", 0.18, 0.21, 0.26, 0.23, confidence=90.0),
            Word("1", 0.27, 0.21, 0.28, 0.23, confidence=92.0),
        ]

    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)
    config = replace(DEFAULT_CONFIG, ocr_drawing_rotations=(0,))
    words = ocr_drawing_words(b"pdf", 0, config)

    assert [word.text for word in words] == ["FIG.1"]
    assert calls == [config.ocr_drawing_figure_psm, config.ocr_drawing_figure_psm]


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


def test_route_page_reuses_one_render_for_full_and_column_ocr(monkeypatch):
    render_calls: list[int] = []
    preprocess_calls: list[tuple[int, int]] = []
    master = Image.new("L", (400, 520), 255)

    def render(_pdf, _index, dpi):
        render_calls.append(dpi)
        return master

    def preprocess(image, _config):
        preprocess_calls.append(image.size)
        return image, 0.0

    full_words = []
    for index in range(20):
        y0 = 0.10 + index * 0.025
        full_words.extend(
            [
                Word(f"left{index}", 0.10, y0, 0.24, y0 + 0.012, confidence=92.0),
                Word(f"right{index}", 0.65, y0, 0.80, y0 + 0.012, confidence=92.0),
            ]
        )

    def recognize(image, _config, *, psm, thresholding_method, char_whitelist=None):
        del image, thresholding_method, char_whitelist
        if psm == 3:
            return full_words
        return [
            Word(
                f"column{index}",
                0.10,
                0.10 + index * 0.05,
                0.30,
                0.12 + index * 0.05,
                confidence=92.0,
            )
            for index in range(8)
        ]

    monkeypatch.setattr(ocr_module, "render_page", render)
    monkeypatch.setattr(ocr_module, "preprocess_image", preprocess)
    monkeypatch.setattr(ocr_module, "_tesseract_words", recognize)

    result = route_page(b"pdf", Page(0, []), DEFAULT_CONFIG, True)

    assert result.is_drawing is False
    assert render_calls == [DEFAULT_CONFIG.ocr_drawing_dpi]
    assert preprocess_calls == [master.size]


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
        replace(DEFAULT_CONFIG, callout_detection_enabled=True),
        True,
        ocr_fn=full_pass,
        drawing_ocr_fn=drawing_pass,
    )
    assert calls == ["drawing"]
    assert result.is_drawing is True
    assert [callout.value for callout in result.callouts] == ["104"]


def test_route_page_keeps_native_figure_label_over_overlapping_ocr_misread():
    native_words = [
        Word("Sheet", 0.40, 0.03, 0.46, 0.05),
        Word("13", 0.47, 0.03, 0.49, 0.05),
        Word("of", 0.50, 0.03, 0.53, 0.05),
        Word("32", 0.54, 0.03, 0.58, 0.05),
        Word("Figure", 0.20, 0.20, 0.27, 0.23),
        Word("9b.", 0.28, 0.20, 0.32, 0.23),
        *[
            Word(str(700 + index), 0.10, 0.30 + index * 0.02, 0.14, 0.31 + index * 0.02)
            for index in range(10)
        ],
    ]

    def drawing_pass(*_args, **_kwargs):
        return [
            Word("Figure", 0.20, 0.20, 0.27, 0.23, confidence=91.0),
            Word("10", 0.28, 0.20, 0.32, 0.23, confidence=89.0),
        ]

    result = route_page(
        b"pdf",
        Page(13, native_words),
        DEFAULT_CONFIG,
        True,
        drawing_ocr_fn=drawing_pass,
    )

    assert [(figure.figure_id, figure.method) for figure in result.figures] == [
        ("9B", "native_text")
    ]


def test_image_only_first_drawing_sheet_keeps_figure_one_without_callouts():
    initial = [
        Word("Sheet", 0.40, 0.03, 0.46, 0.05),
        Word("1", 0.47, 0.03, 0.49, 0.05),
        Word("of", 0.50, 0.03, 0.53, 0.05),
        Word("32", 0.54, 0.03, 0.58, 0.05),
    ]

    def full_pass(*_args, **_kwargs):
        return initial

    def drawing_pass(*_args, **_kwargs):
        return [
            Word("Figure", 0.18, 0.21, 0.26, 0.23, confidence=90.0),
            Word("1", 0.27, 0.21, 0.28, 0.23, confidence=92.0),
            Word("14", 0.30, 0.40, 0.33, 0.43, confidence=94.0),
        ]

    result = route_page(
        b"pdf",
        Page(1, []),
        DEFAULT_CONFIG,
        True,
        ocr_fn=full_pass,
        drawing_ocr_fn=drawing_pass,
    )

    assert [(figure.figure_id, figure.page_index) for figure in result.figures] == [("1", 1)]
    assert result.is_drawing_sheet is True
    assert result.callouts == ()


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


def test_ocr_paragraph_hierarchy_alone_does_not_create_random_breaks():
    words = [
        Word("1", 0.28, 0.045, 0.30, 0.062, confidence=95.0),
        Word("2", 0.70, 0.045, 0.72, 0.062, confidence=95.0),
    ]
    for i in range(10):
        cy = 0.11 + 0.025 * i
        paragraph = 1 if i < 5 else 2
        words += [
            Word(
                "line",
                0.12,
                cy - 0.008,
                0.20,
                cy + 0.008,
                confidence=90.0,
                block_num=1,
                paragraph_num=paragraph,
                line_num=i + 1,
            ),
            Word(
                str(i),
                0.22,
                cy - 0.008,
                0.25,
                cy + 0.008,
                confidence=90.0,
                block_num=1,
                paragraph_num=paragraph,
                line_num=i + 1,
            ),
            Word(
                "right",
                0.55,
                cy - 0.008,
                0.66,
                cy + 0.008,
                confidence=90.0,
                block_num=2,
                paragraph_num=1,
                line_num=i + 1,
            ),
        ]
        if (i + 1) % 5 == 0:
            words.append(Word(str(i + 1), 0.49, cy - 0.008, 0.51, cy + 0.008, 99.0))

    art = extract_from_pages(
        [Page(0, words)], DEFAULT_CONFIG, source_sha256="x", page_methods=["ocr"]
    )
    left = [entry for entry in art.entries if entry.locator.column == 1]
    assert left[0].paragraph_start is False
    assert left[5].paragraph_start is False
