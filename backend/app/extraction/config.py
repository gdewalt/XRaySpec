"""Versioned extraction configuration (DESIGN.md §20, §25.3).

Every tunable threshold, tolerance, and resolution lives here in one frozen
object rather than scattered as magic numbers. Its ``config_hash`` is part of the
extraction cache key and the reproducibility anchor: two runs with the same
source bytes and the same config must produce semantically equivalent artifacts.

Bump ``version`` whenever a change alters extraction output; the hash then
invalidates cached artifacts and checkpoints keyed on it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

OcrPolicy = Literal["auto", "force", "disabled"]


@dataclass(frozen=True, slots=True)
class ExtractionConfig:
    # Engine identity
    version: str = "0.10.0"

    # OCR
    ocr_policy: OcrPolicy = "auto"
    ocr_dpi: int = 300
    ocr_drawing_dpi: int = 400
    ocr_languages: tuple[str, ...] = ("eng",)
    ocr_min_confidence: float = 0.0  # keep all non-empty tokens by default
    min_native_words_per_page: int = 15  # below this a page is treated as image-only
    ocr_text_psm: int = 6  # one uniform block after each specification column is cropped
    ocr_text_retry_psm: int = 4  # retry weak text as variable-size column blocks
    ocr_sparse_psm: int = 11  # Tesseract page-seg mode for drawing callout labels (§12.7)
    ocr_retry_mean_confidence: float = 78.0
    ocr_retry_low_confidence_fraction: float = 0.20
    ocr_retry_low_confidence_cutoff: float = 50.0
    ocr_deskew_max_degrees: float = 2.0
    ocr_deskew_step_degrees: float = 0.5
    ocr_drawing_rotations: tuple[int, ...] = (0, 90, 180, 270)

    # Grant line-reference reconstruction (§12.5)
    line_y_tolerance: float = 3.0
    gutter_max_spread: float = 5.0
    lines_per_column: int = 65
    content_top_margin: float = 0.06  # drop the running header band
    content_bottom_margin: float = 0.95  # drop the page-number/footer band

    # Clean-text alignment (§13)
    alignment_min_ratio: float = 0.72  # below this, keep source_text (reject substitution)
    alignment_exact_ratio: float = 0.97  # at/above this, call it an exact match
    alignment_search_slack: int = 8  # forward token window when locating a line

    # Safety bounds re-enforced in the worker (§11.1, §17.6)
    max_pages: int = 2000
    max_pixels_per_page: int = 40_000_000

    def config_hash(self) -> str:
        """Stable SHA-256 over the config, for cache/checkpoint keying."""
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_CONFIG = ExtractionConfig()
