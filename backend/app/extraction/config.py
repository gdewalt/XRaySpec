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
    version: str = "0.1.0"

    # OCR
    ocr_policy: OcrPolicy = "auto"
    ocr_dpi: int = 300
    ocr_languages: tuple[str, ...] = ("eng",)

    # Grant line-reference reconstruction (§12.5)
    line_y_tolerance: float = 3.0
    gutter_max_spread: float = 5.0
    lines_per_column: int = 65

    # Safety bounds re-enforced in the worker (§11.1, §17.6)
    max_pages: int = 2000
    max_pixels_per_page: int = 40_000_000

    def config_hash(self) -> str:
        """Stable SHA-256 over the config, for cache/checkpoint keying."""
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_CONFIG = ExtractionConfig()
