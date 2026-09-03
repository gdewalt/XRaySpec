"""Clean-room extraction core (DESIGN.md §12, §25.1, §25.3.1).

The single pure seam between "a PDF" and "an immutable artifact" — no web layer,
no database, no global state. Deterministic and geometry-anchored: models may
*propose*, geometry must *confirm*, and ``source_text`` is never overwritten by a
model (§25.1).

This first cut does native grant ``col:line`` extraction (§12.5). OCR fallback,
applications (paragraphs), figures, and callouts land in later slices behind this
same entry point.
"""

from __future__ import annotations

import hashlib

from .artifact import Artifact
from .config import ExtractionConfig
from .model import Page
from .native import extract_page


def extract_from_pages(
    pages: list[Page], config: ExtractionConfig, *, source_sha256: str
) -> Artifact:
    """Assemble an immutable artifact from already-parsed pages (pure, testable)."""
    entries = []
    ordinal = 0
    for page in pages:
        page_entries, ordinal = extract_page(page, config, ordinal)
        entries.extend(page_entries)

    detected = sum(1 for e in entries if e.provenance.reference_method == "detected")
    interpolated = sum(1 for e in entries if e.provenance.reference_method == "interpolated")
    warnings: list[str] = []
    if entries and detected == 0:
        warnings.append("No printed gutter line-numbers detected; references are unanchored.")

    disposition = "complete" if entries and not warnings else (
        "complete_with_warnings" if entries else "partial"
    )

    return Artifact(
        schema_version=2,
        source_sha256=source_sha256,
        doc_type="grant",
        page_count=len(pages),
        engine_version=config.version,
        config_hash=config.config_hash(),
        mode="native",
        disposition=disposition,
        entries=entries,
        quality={
            "page_count": len(pages),
            "entry_count": len(entries),
            "detected_references": detected,
            "interpolated_references": interpolated,
        },
        warnings=warnings,
    )


def extract(pdf_bytes: bytes, config: ExtractionConfig) -> Artifact:
    """Extract an immutable :class:`Artifact` from raw PDF bytes (native pass)."""
    from .pdf import load_pages  # lazy: pdfplumber only needed at runtime

    source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    pages = load_pages(pdf_bytes)
    return extract_from_pages(pages, config, source_sha256=source_sha256)
