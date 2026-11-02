"""AI review validation stays conservative and deterministic around model output."""

from __future__ import annotations

from difflib import SequenceMatcher

from app.config import Settings
from app.services.ai_scan import (
    StructuredResponseIncomplete,
    _is_conservative_text_correction,
    _prune_crowded_unsupported_figures,
    _rebuild_associations,
    _review_text_batch,
    _safe_box,
    review_text,
)


class _FakeClient:
    async def structured(self, **_kwargs):
        return {
            "corrections": [
                {
                    "entry_id": "line-1",
                    "corrected_text": (
                        "A Driver Software Interface determines the route. "
                        "FIG. 6b uses interface 104."
                    ),
                    "confidence": 0.98,
                    "reason": "Removed duplicated OCR fragment",
                },
                {
                    "entry_id": "line-2",
                    "corrected_text": "Completely rewritten legal prose.",
                    "confidence": 0.99,
                    "reason": "Rewrite",
                },
            ],
            "figure_mentions": [
                {
                    "entry_id": "line-1",
                    "raw_text": "FIG. 6b",
                    "figure_ids": ["6B"],
                    "confidence": 0.95,
                }
            ],
            "callout_mentions": [
                {
                    "entry_id": "line-1",
                    "value": "104",
                    "component_label": "interface",
                    "confidence": 0.95,
                }
            ],
        }


class _TruncatingClient:
    def __init__(self, *, always: bool = False):
        self.calls = 0
        self.always = always

    async def structured(self, **_kwargs):
        self.calls += 1
        if self.always or self.calls == 1:
            raise StructuredResponseIncomplete("truncated")
        return {"corrections": [], "figure_mentions": [], "callout_mentions": []}


async def test_text_review_applies_only_close_high_confidence_corrections():
    progress_events: list[tuple[str, str, int, int]] = []

    async def record_progress(stage, label, completed, total):
        progress_events.append((stage, label, completed, total))

    payload = {
        "entries": [
            {
                "entry_id": "line-1",
                "display_text": (
                    "A Driver Software Interface ace determines the route. "
                    "FIG. 6b uses interface 104."
                ),
                "source_text": "original",
                "provenance": {},
            },
            {
                "entry_id": "line-2",
                "display_text": (
                    "A long and legally meaningful sentence that must remain unchanged."
                ),
                "source_text": "original two",
                "provenance": {},
            },
        ],
        "figure_mentions": [],
        "numeral_mentions": [],
    }
    summary = await review_text(
        _FakeClient(),
        payload,
        Settings(openai_api_key="test", ai_scan_text_chunk_chars=20_000),
        on_progress=record_progress,
        total_units=2,
    )
    assert payload["entries"][0]["display_text"].startswith(
        "A Driver Software Interface determines"
    )
    assert payload["entries"][0]["source_text"] == "original"
    assert payload["entries"][1]["display_text"].startswith("A long and legally meaningful")
    assert summary["corrected_lines"] == 1
    assert payload["figure_mentions"][0]["figure_ids"] == ["6b"]
    assert payload["numeral_mentions"][0]["value"] == "104"
    assert progress_events == [
        ("ai_scan_text", "Reviewing patent text (1/1)", 1, 2),
    ]


def test_ai_cleanup_rejects_appended_alternate_rendering_of_patent_line():
    original = (
        "processes of the invention also enable the fabricator to reduce the "
        "cross-section, thus limiting the effect of wind resistance and "
        "lightening the racquet."
    )
    expanded = f"{original} and lightning the racquet."

    assert SequenceMatcher(None, original, expanded).ratio() >= 0.72
    assert _is_conservative_text_correction(original, expanded) is False


def test_ai_cleanup_allows_small_word_repairs_and_duplicate_removal():
    assert _is_conservative_text_correction(
        "A Driver Software Interface ace determines the route.",
        "A Driver Software Interface determines the route.",
    )
    assert _is_conservative_text_correction(
        "the teh racquet is light",
        "the racquet is light",
    )


class _HallucinatedFigureClient:
    async def structured(self, **_kwargs):
        return {
            "corrections": [],
            "figure_mentions": [
                {
                    "entry_id": "line-1",
                    "raw_text": "55",
                    "figure_ids": ["55"],
                    "confidence": 0.99,
                },
                {
                    "entry_id": "line-1",
                    "raw_text": "FIG. 6b",
                    "figure_ids": ["66"],
                    "confidence": 0.99,
                },
            ],
            "callout_mentions": [],
        }


async def test_text_review_requires_visible_figure_prefix_and_derives_id_from_phrase():
    payload = {
        "entries": [
            {
                "entry_id": "line-1",
                "display_text": "Element 55 is shown in FIG. 6b.",
                "source_text": "Element 55 is shown in FIG. 6b.",
                "provenance": {},
            }
        ],
        "figure_mentions": [],
        "numeral_mentions": [],
    }

    await review_text(
        _HallucinatedFigureClient(),
        payload,
        Settings(openai_api_key="test", ai_scan_text_chunk_chars=20_000),
    )

    assert [mention["figure_ids"] for mention in payload["figure_mentions"]] == [["6b"]]


def test_crowded_unsupported_figure_cluster_is_removed_from_one_drawing_page():
    occurrences = [
        {"figure_id": figure_id, "page_index": 33}
        for figure_id in ("14i", "55", "65", "66", "84", "105", "145")
    ]
    occurrences.append({"figure_id": "15", "page_index": 34})

    kept = _prune_crowded_unsupported_figures(occurrences, {"14i"})

    assert kept == [
        {"figure_id": "14i", "page_index": 33},
        {"figure_id": "15", "page_index": 34},
    ]


def test_ai_boxes_and_associations_are_validated():
    assert _safe_box([100, 200, 300, 400]) == [0.1, 0.2, 0.3, 0.4]
    assert _safe_box([300, 200, 100, 400]) is None
    payload = {
        "numeral_mentions": [
            {"entry_id": "line-1", "value": "104", "span": [10, 13]},
        ],
        "callout_occurrences": [
            {"callout_id": "ai:2:104:0", "value": "104"},
        ],
    }
    _rebuild_associations(payload)
    assert payload["mention_associations"][0]["status"] == "verified"
    assert payload["mention_associations"][0]["selected_callout_ids"] == ["ai:2:104:0"]


async def test_truncated_text_batch_is_bisected_without_failing_scan():
    client = _TruncatingClient()
    results, skipped = await _review_text_batch(
        client,
        [
            {"entry_id": "line-1", "text": "first"},
            {"entry_id": "line-2", "text": "second"},
        ],
    )
    assert client.calls == 3
    assert len(results) == 2
    assert skipped == 0


async def test_single_truncated_line_keeps_original_and_is_counted():
    results, skipped = await _review_text_batch(
        _TruncatingClient(always=True),
        [{"entry_id": "line-1", "text": "original"}],
    )
    assert results == []
    assert skipped == 1
