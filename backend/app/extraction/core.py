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
from collections.abc import Callable
from dataclasses import dataclass

from .applications import count_paragraph_markers, extract_application_page
from .artifact import Artifact, CalloutOccurrence, FigureOccurrence
from .callouts import (
    assign_callouts_to_figures,
    associate_mentions,
    detect_callouts,
    detect_figure_occurrences,
    detect_page_figure,
    filter_callouts_by_values,
    filter_figure_occurrences,
    is_drawing_page,
    specification_callout_values,
)
from .config import ExtractionConfig
from .figures import detect_figure_references, detect_reference_numerals
from .model import Page, Word
from .native import extract_page

_MARKER_THRESHOLD = 3  # a document with this many paragraph markers is an application


@dataclass(frozen=True, slots=True)
class PageResult:
    """The per-page text result (DESIGN.md §10.7 ``page_text``): a specification
    page's words *or* a drawing page's callouts. This is the checkpointable unit —
    the expensive (OCR) work — that a resume reuses; the downstream pipeline over
    these results is always recomputed fresh."""

    page_index: int
    method: str  # "native" | "ocr"
    is_drawing: bool
    words: tuple[Word, ...] = ()  # specification words (empty for a drawing page)
    figures: tuple[FigureOccurrence, ...] = ()  # every FIG label on a drawing page
    callouts: tuple[CalloutOccurrence, ...] = ()  # drawing callouts (empty for a spec page)


def detect_doc_type(pages: list[Page]) -> str:
    """Classify grant (``col:line``) vs application (paragraph) by marker density."""
    return "application" if count_paragraph_markers(pages) >= _MARKER_THRESHOLD else "grant"


def route_page(
    pdf_bytes: bytes,
    native_page: Page,
    config: ExtractionConfig,
    can_ocr: bool,
    *,
    ocr_fn: Callable[..., list[Word]] | None = None,
    specification_ocr_fn: Callable[..., list[Word]] | None = None,
    drawing_ocr_fn: Callable[..., list[Word]] | None = None,
) -> PageResult:
    """Route one page to native words or OCR, and classify spec-vs-drawing (§12.3–12.7).

    Pure per-page work with no persistence: the resume orchestrator calls this only
    for pages without a valid ``page_text`` checkpoint."""
    if ocr_fn is None:
        # Lazy imports keep PDF/OCR binaries out of API and unit-test startup.
        from .ocr import ocr_drawing_words, ocr_page_words, ocr_specification_words

        ocr_fn = ocr_page_words
        specification_ocr_fn = specification_ocr_fn or ocr_specification_words
        drawing_ocr_fn = drawing_ocr_fn or ocr_drawing_words

    if len(native_page.words) >= config.min_native_words_per_page:
        page, method = native_page, "native"
    elif can_ocr:
        words = ocr_fn(pdf_bytes, native_page.index, config)
        page, method = Page(index=native_page.index, words=words), "ocr"
    else:
        page, method = native_page, "native"  # no text layer, no OCR

    if is_drawing_page(page.words):
        draw_words = page.words
        if can_ocr and drawing_ocr_fn is not None:
            enhanced = drawing_ocr_fn(pdf_bytes, page.index, config)
            if method == "native":
                from .ocr import dedupe_words

                draw_words = dedupe_words([*draw_words, *enhanced])
            else:
                draw_words = enhanced
        elif method == "ocr" and can_ocr:
            draw_words = ocr_fn(pdf_bytes, page.index, config, psm=config.ocr_sparse_psm)
        figure_id = detect_page_figure(draw_words)
        figures = tuple(detect_figure_occurrences(draw_words, page.index))
        callouts = tuple(detect_callouts(draw_words, page.index, figure_id))
        return PageResult(page.index, method, True, figures=figures, callouts=callouts)
    if method == "ocr" and specification_ocr_fn is not None:
        words = specification_ocr_fn(pdf_bytes, page.index, config, list(page.words))
        page = Page(index=page.index, words=words)
    return PageResult(page.index, method, False, words=tuple(page.words))


def assemble_from_results(
    results: list[PageResult],
    config: ExtractionConfig,
    *,
    source_sha256: str,
    doc_type: str,
    total_pages: int,
) -> Artifact:
    """Recompute the fast downstream pipeline over per-page results (§10.7)."""
    ordered = sorted(results, key=lambda r: r.page_index)
    spec = [r for r in ordered if not r.is_drawing]
    spec_pages = [Page(index=r.page_index, words=list(r.words)) for r in spec]
    methods = [r.method for r in spec]
    figure_occurrences = [f for r in ordered if r.is_drawing for f in r.figures]
    callouts = [c for r in ordered if r.is_drawing for c in r.callouts]
    if doc_type not in ("grant", "application"):
        doc_type = detect_doc_type(spec_pages)
    return extract_from_pages(
        spec_pages,
        config,
        source_sha256=source_sha256,
        page_methods=methods,
        doc_type=doc_type,
        figure_occurrences=figure_occurrences,
        callouts=callouts,
        total_pages=total_pages,
    )


def extract_from_pages(
    pages: list[Page],
    config: ExtractionConfig,
    *,
    source_sha256: str,
    page_methods: list[str] | None = None,
    doc_type: str = "grant",
    figure_occurrences: list[FigureOccurrence] | None = None,
    callouts: list[CalloutOccurrence] | None = None,
    total_pages: int | None = None,
) -> Artifact:
    """Assemble an immutable artifact from parsed pages (pure, testable).

    ``pages`` are the specification pages; ``callouts`` are pre-detected drawing
    callouts (drawing pages are handled by ``extract``). ``page_methods[i]`` is
    ``"native"`` or ``"ocr"`` for ``pages[i]``; ``doc_type`` selects the
    reconstruction (grant col:line or application paragraph).
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
        fallback_columns = (1, 2)
        for page, method in zip(pages, methods, strict=False):
            page_entries, ordinal = extract_page(
                page,
                config,
                ordinal,
                method=method,
                fallback_columns=fallback_columns,
            )
            entries.extend(page_entries)
            if page_entries:
                last_column = max(entry.locator.column for entry in page_entries)
                fallback_columns = (last_column + 1, last_column + 2)

    figure_mentions = detect_figure_references(entries)
    numeral_mentions = detect_reference_numerals(entries)
    expected_figure_ids = {
        figure_id.upper()
        for mention in figure_mentions
        for figure_id in mention.figure_ids
    }
    drawing_figures = filter_figure_occurrences(
        figure_occurrences or [], expected_figure_ids
    )
    supported_callouts = specification_callout_values(
        pages,
        fallback_values=(mention.value for mention in numeral_mentions),
    )
    callout_occurrences = assign_callouts_to_figures(
        filter_callouts_by_values(callouts or [], supported_callouts),
        drawing_figures,
    )
    mention_associations = associate_mentions(
        numeral_mentions, callout_occurrences, figure_mentions, entries
    )

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
        page_count=total_pages if total_pages is not None else len(pages),
        engine_version=config.version,
        config_hash=config.config_hash(),
        mode=mode,
        disposition=disposition,
        entries=entries,
        figure_mentions=figure_mentions,
        numeral_mentions=numeral_mentions,
        figure_occurrences=drawing_figures,
        callout_occurrences=callout_occurrences,
        mention_associations=mention_associations,
        quality={
            "page_count": total_pages if total_pages is not None else len(pages),
            "ocr_pages": ocr_pages,
            "entry_count": len(entries),
            "detected_references": detected,
            "interpolated_references": interpolated,
            "figure_mentions": len(figure_mentions),
            "figure_occurrences": len(drawing_figures),
            "numeral_mentions": len(numeral_mentions),
            "callouts": len(callout_occurrences),
            "verified_links": sum(1 for a in mention_associations if a.status == "verified"),
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
    from .ocr import ocr_available
    from .pdf import load_pages

    source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    native_pages = load_pages(pdf_bytes)
    can_ocr = ocr_available()

    results = [route_page(pdf_bytes, np, config, can_ocr) for np in native_pages]
    return assemble_from_results(
        results,
        config,
        source_sha256=source_sha256,
        doc_type=doc_type,
        total_pages=len(native_pages),
    )
