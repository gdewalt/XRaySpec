"""Seed corpus of labeled cases (DESIGN.md §19.1).

Synthetic, hand-labeled fixtures that exercise the harness end to end without the
PDF/OCR libraries or copyrighted PDFs — so the golden gates run in CI today. Each
builder returns a :class:`Case` (an extraction artifact + its ground-truth labels,
plus any separately detected callouts). Real labeled patents drop in as
``corpus/*.json`` and are loaded through the same :class:`DocumentLabels` schema;
the synthetic cases here are the mechanism, not the accuracy claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..enrichment.align import align_entries
from ..extraction.artifact import Artifact, CalloutOccurrence, Entry, Provenance
from ..extraction.callouts import detect_callouts
from ..extraction.config import DEFAULT_CONFIG, ExtractionConfig
from ..extraction.core import extract_from_pages
from ..extraction.locator import GrantLocator
from ..extraction.model import Page, Word
from .labels import AssociationLabel, CalloutLabel, DocumentLabels, ReferenceLabel


@dataclass(frozen=True, slots=True)
class Case:
    artifact: Artifact
    labels: DocumentLabels
    detected_callouts: list[CalloutOccurrence] = field(default_factory=list)


# --- grant printed-line reference accuracy (§12.5, §19.2) ---------------------

def _grant_page(n_rows: int = 15) -> Page:
    """A two-column grant page: column-number header + centre-gutter numbers (×5)."""
    words = [Word("1", 0.28, 0.045, 0.30, 0.062), Word("2", 0.70, 0.045, 0.72, 0.062)]
    for i in range(n_rows):
        cy = 0.11 + 0.025 * i
        y0, y1 = cy - 0.008, cy + 0.008
        words += [
            Word("The", 0.12, y0, 0.30, y1),
            Word("housing", 0.32, y0, 0.44, y1),
            Word("right", 0.55, y0, 0.72, y1),
            Word("side", 0.74, y0, 0.88, y1),
        ]
        if (i + 1) % 5 == 0:
            words.append(Word(str(i + 1), 0.49, y0, 0.51, y1))
    return Page(index=0, words=words)


def grant_reference_case(config: ExtractionConfig = DEFAULT_CONFIG, *, n_lines: int = 15) -> Case:
    artifact = extract_from_pages([_grant_page(n_lines)], config, source_sha256="grant-seed")
    references = tuple(
        ReferenceLabel(
            ordinal=e.ordinal,
            column=e.locator.column,
            printed_line=e.locator.printed_line,
            box=e.box,
        )
        for e in artifact.entries
    )
    labels = DocumentLabels(
        doc_id="seed-grant",
        doc_type="grant",
        spec_page_indices=(0,),
        references=references,
        notes="Two-column grant; centre-gutter line numbers every 5th row.",
    )
    return Case(artifact=artifact, labels=labels)


# --- drawing callout detection (§12.7, §19.2) ---------------------------------

def callout_case() -> Case:
    """A drawing sheet with three numeral callouts and one excluded year."""
    boxes = {
        "104": (0.20, 0.30, 0.24, 0.33),
        "106": (0.55, 0.40, 0.59, 0.43),
        "108": (0.70, 0.60, 0.74, 0.63),
    }
    words = [Word("FIG", 0.45, 0.05, 0.50, 0.08), Word("3", 0.51, 0.05, 0.53, 0.08)]
    for value, (x0, y0, x1, y1) in boxes.items():
        words.append(Word(value, x0, y0, x1, y1))
    words.append(Word("2019", 0.10, 0.90, 0.16, 0.93))  # a year, not a callout

    detected = detect_callouts(words, page_index=0, figure_id="3")
    labels = DocumentLabels(
        doc_id="seed-callouts",
        doc_type="grant",
        callouts=tuple(CalloutLabel(value=v, box=b, page_index=0) for v, b in boxes.items()),
        notes="One drawing sheet; three callouts; a year that must be excluded.",
    )
    # An artifact carrying the detected callouts (as the drawing side would).
    artifact = Artifact(
        schema_version=2, source_sha256="callout-seed", doc_type="grant", page_count=1,
        engine_version=DEFAULT_CONFIG.version, config_hash=DEFAULT_CONFIG.config_hash(),
        mode="ocr", disposition="complete", callout_occurrences=detected,
    )
    return Case(artifact=artifact, labels=labels, detected_callouts=detected)


# --- wrong-document substitution rejection (§13.2, §19.2) ---------------------

def _entry(text: str, ordinal: int) -> Entry:
    return Entry(
        entry_id=f"line_{ordinal:07d}",
        ordinal=ordinal,
        page_index=0,
        locator=GrantLocator(column=1, printed_line=ordinal + 1),
        box=(0.1, 0.1, 0.9, 0.12),
        source_text=text,
        display_text=text,
        provenance=Provenance(extraction_method="ocr", ocr_confidence=80.0),
    )


def wrong_document_case() -> Case:
    """OCR lines aligned against a DIFFERENT patent's clean text — nothing may be
    substituted (100% rejection). Aligned here directly (no fetch)."""
    entries = [
        _entry("The rotor spins about the central axis.", 0),
        _entry("A bearing supports the rotor shaft.", 1),
    ]
    wrong_clean = "A wireless base station transmits random access preambles to a user."
    aligned = align_entries(entries, wrong_clean, DEFAULT_CONFIG, identity_verified=False)
    artifact = Artifact(
        schema_version=2, source_sha256="wrongdoc-seed", doc_type="grant", page_count=1,
        engine_version=DEFAULT_CONFIG.version, config_hash=DEFAULT_CONFIG.config_hash(),
        mode="ocr", disposition="complete", entries=aligned,
    )
    labels = DocumentLabels(
        doc_id="seed-wrongdoc",
        doc_type="grant",
        wrong_document=True,
        notes="Clean text is from another patent; correct engine substitutes nothing.",
    )
    return Case(artifact=artifact, labels=labels)


# --- ambiguous association (§12.7 / Q20) --------------------------------------

def ambiguous_association_case() -> Case:
    """A numeral mention with two out-of-context callouts of the same value must be
    ``ambiguous`` — never a silent ``verified`` destination."""
    from ..extraction.artifact import MentionAssociation

    assoc = [
        MentionAssociation(
            entry_id="line_0000000", value="104", span=(0, 3), status="ambiguous",
            candidate_callout_ids=["callout_0001_0001", "callout_0002_0001"],
        )
    ]
    artifact = Artifact(
        schema_version=2, source_sha256="assoc-seed", doc_type="grant", page_count=1,
        engine_version=DEFAULT_CONFIG.version, config_hash=DEFAULT_CONFIG.config_hash(),
        mode="ocr", disposition="complete", mention_associations=assoc,
    )
    labels = DocumentLabels(
        doc_id="seed-ambiguous",
        doc_type="grant",
        associations=(AssociationLabel(entry_ordinal=0, value="104", status="ambiguous"),),
        notes="Same numeral appears in two figures; association must stay ambiguous.",
    )
    return Case(artifact=artifact, labels=labels)


def seed_corpus() -> list[Case]:
    """The full synthetic seed corpus."""
    return [
        grant_reference_case(),
        callout_case(),
        wrong_document_case(),
        ambiguous_association_case(),
    ]
