"""Clean-room extraction core (DESIGN.md §12, §25.1, §25.3.1).

The single pure seam between "a PDF" and "an immutable artifact" — no web layer,
no database, no global state. Deterministic and geometry-anchored: models may
*propose*, geometry must *confirm*, and ``source_text`` is never overwritten by a
model (§25.1).

Native grant ``col:line`` with per-page OCR fallback (§12.3–12.4): pages with a
usable text layer are read natively; image-only pages are rendered and OCR'd, and
both feed the same line-reconstruction. Applications (paragraphs), figures, and
callouts land in later slices behind this same entry point.
"""

from __future__ import annotations

import hashlib

from .applications import count_paragraph_markers, extract_application_page
from .artifact import Artifact
from .config import ExtractionConfig
from .model import Page
from .native import extract_page

_MARKER_THRESHOLD = 3  # a document with this many paragraph markers is an application


def detect_doc_type(pages: list[Page]) -> str:
    """Classify grant (``col:line``) vs application (paragraph) by marker density."""
    return "application" if count_paragraph_markers(pages) >= _MARKER_THRESHOLD else "grant"


def extract_from_pages(
    pages: list[Page],
    config: ExtractionConfig,
    *,
    source_sha256: str,
    page_methods: list[str] | None = None,
    doc_type: str = "grant",
) -> Artifact:
    """Assemble an immutable artifact from parsed pages (pure, testable).

    ``page_methods[i]`` is ``"native"`` or ``"ocr"`` for ``pages[i]`` (default all
    native). ``doc_type`` selects the reconstruction: ``"grant"`` (col:line) or
    ``"application"`` (paragraph markers).
    """
    methods = page_methods or ["native"] * len(pages)
    entries = []
    ordinal = 0
    if doc_type == "application":
        state: dict = {"paragraph": None}
        for page, method in zip(pages, methods, strict=False):
            page_entries, ordinal, state = extract_application_page(
                page, config, ordinal, method, state
            )
            entries.extend(page_entries)
    else:
        for page, method in zip(pages, methods, strict=False):
            page_entries, ordinal = extract_page(page, config, ordinal, method=method)
            entries.extend(page_entries)

    detected = sum(1 for e in entries if e.provenance.reference_method == "detected")
    interpolated = sum(1 for e in entries if e.provenance.reference_method == "interpolated")
    ocr_pages = sum(1 for m in methods if m == "ocr")

    warnings: list[str] = []
    if entries and detected == 0:
        label = "paragraph markers" if doc_type == "application" else "printed gutter line-numbers"
        warnings.append(f"No {label} detected; references are unanchored.")

    if not entries:
        disposition = "partial"
    elif warnings:
        disposition = "complete_with_warnings"
    else:
        disposition = "complete"

    if ocr_pages == 0:
        mode = "native"
    elif ocr_pages == len(methods):
        mode = "ocr"
    else:
        mode = "hybrid"

    return Artifact(
        schema_version=2,
        source_sha256=source_sha256,
        doc_type=doc_type if doc_type in ("grant", "application") else "grant",
        page_count=len(pages),
        engine_version=config.version,
        config_hash=config.config_hash(),
        mode=mode,
        disposition=disposition,
        entries=entries,
        quality={
            "page_count": len(pages),
            "ocr_pages": ocr_pages,
            "entry_count": len(entries),
            "detected_references": detected,
            "interpolated_references": interpolated,
        },
        warnings=warnings,
    )


def extract(pdf_bytes: bytes, config: ExtractionConfig, doc_type: str = "auto") -> Artifact:
    """Extract an immutable :class:`Artifact` from raw PDF bytes.

    Routes each page: native words if the page has a usable text layer, otherwise
    render + OCR (when Tesseract is available). ``doc_type`` is ``"grant"``,
    ``"application"``, or ``"auto"`` (classify by paragraph-marker density). Runs
    only in the isolated worker.
    """
    from .ocr import ocr_available, ocr_page_words
    from .pdf import load_pages

    source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    native_pages = load_pages(pdf_bytes)
    can_ocr = ocr_available()

    pages: list[Page] = []
    methods: list[str] = []
    for native_page in native_pages:
        if len(native_page.words) >= config.min_native_words_per_page:
            pages.append(native_page)
            methods.append("native")
        elif can_ocr:
            words = ocr_page_words(pdf_bytes, native_page.index, config)
            pages.append(Page(index=native_page.index, words=words))
            methods.append("ocr")
        else:
            pages.append(native_page)  # no text layer, no OCR -> yields no entries
            methods.append("native")

    if doc_type not in ("grant", "application"):
        doc_type = detect_doc_type(pages)

    return extract_from_pages(
        pages, config, source_sha256=source_sha256, page_methods=methods, doc_type=doc_type
    )
