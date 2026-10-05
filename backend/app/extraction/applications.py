"""Application-publication paragraph reconstruction (DESIGN.md §7.2, §12.5).

Application publications address text by paragraph number (``[0001]``) printed
inline at the start of each paragraph — not by ``col:line``. This is *easier* than
grants: the markers are explicit, so no gutter fit is needed. Each line is tagged
with the paragraph it belongs to; the marker is stripped from the body and the
current paragraph persists across lines and pages.

Pure over the abstract ``Page``/``Word`` model, sharing ``group_lines`` with the
grant path. Single-column first cut; multi-column applications are a later
refinement.
"""

from __future__ import annotations

import re

from .artifact import Entry, Provenance
from .config import ExtractionConfig
from .locator import ApplicationLocator
from .model import Page
from .native import _clamp_box, _median, group_lines

# A paragraph marker: a 3–4 digit number in [] or () at the start of a line, e.g.
# "[0001]" or "(0042)".
_LINE_MARKER = re.compile(r"^[\[(]\s*(\d{3,4})\s*[\])]\s*")
# A whole-token marker, for document-type detection.
_TOKEN_MARKER = re.compile(r"^[\[(]\d{3,4}[\])]$")


def looks_like_paragraph_token(text: str) -> bool:
    return bool(_TOKEN_MARKER.match(text.strip()))


def extract_application_page(
    page: Page,
    config: ExtractionConfig,
    ordinal: int,
    method: str,
    state: dict,
) -> tuple[list[Entry], int, dict]:
    """Emit paragraph-tagged entries. ``state['paragraph']`` carries the current
    paragraph across lines and pages."""
    words = [
        w for w in page.words if config.content_top_margin <= w.cy <= config.content_bottom_margin
    ]
    lines = group_lines(words)
    common_left = _median([line.x0 for line in lines])

    entries: list[Entry] = []
    for ln in lines:
        text = " ".join(w.text for w in ln.words).strip()
        if not text:
            continue

        marker = _LINE_MARKER.match(text)
        if marker:
            state["paragraph"] = marker.group(1)
            text = text[marker.end() :].strip()
            ref_method, ref_conf = "detected", "high"
            if not text:  # a marker alone on its own line
                continue
        elif state.get("paragraph"):
            ref_method, ref_conf = "interpolated", "medium"
        else:
            ref_method, ref_conf = "none", "low"

        paragraph = state.get("paragraph") or "0000"
        box = _clamp_box(
            min(w.x0 for w in ln.words),
            min(w.y0 for w in ln.words),
            max(w.x1 for w in ln.words),
            max(w.y1 for w in ln.words),
        )
        confidences = [w.confidence for w in ln.words if w.confidence is not None]
        ocr_confidence = (sum(confidences) / len(confidences)) if confidences else None
        entries.append(
            Entry(
                entry_id=f"line_{ordinal:07d}",
                ordinal=ordinal,
                page_index=page.index,
                locator=ApplicationLocator(paragraph=paragraph),
                box=box,
                source_text=text,
                display_text=text,
                provenance=Provenance(
                    extraction_method=method,
                    ocr_confidence=ocr_confidence,
                    reference_method=ref_method,
                ),
                text_confidence="high" if method == "native" else "medium",
                reference_confidence=ref_conf,
                paragraph_start=marker is not None,
                indent_level=max(0, min(6, round((ln.x0 - common_left) / 0.018))),
            )
        )
        ordinal += 1
    return entries, ordinal, state


def count_paragraph_markers(pages: list[Page]) -> int:
    return sum(
        1 for page in pages for w in page.words if looks_like_paragraph_token(w.text)
    )
