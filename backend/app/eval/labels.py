"""Corpus ground-truth label schema (DESIGN.md §19.1).

A labeled corpus document records what a *correct* extraction should produce, so
the harness can score the engine's actual output against it. Kept as plain frozen
dataclasses with JSON (de)serialization: a real corpus lives as ``corpus/*.json``
files (see ``corpus/README.md``), annotated once and version-controlled, while the
seed corpus is built in code (``app.eval.cases``).

References are matched to extracted entries by ``ordinal`` (0-based reading order),
the same stable sequence the engine assigns; boxes use the normalized top-left
convention (§8.2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..extraction.artifact import Box


@dataclass(frozen=True, slots=True)
class ReferenceLabel:
    """The correct locator (and optional box) for the entry at ``ordinal``."""

    ordinal: int
    column: int | None = None  # grant col:line
    printed_line: int | None = None
    paragraph: int | None = None  # application [NNNN]
    box: Box | None = None  # expected line box, for IoU

    @classmethod
    def from_dict(cls, d: dict) -> ReferenceLabel:
        box = tuple(d["box"]) if d.get("box") is not None else None
        return cls(
            ordinal=d["ordinal"],
            column=d.get("column"),
            printed_line=d.get("printed_line"),
            paragraph=d.get("paragraph"),
            box=box,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class CalloutLabel:
    """A drawing callout that should be detected: numeral value + region."""

    value: str
    box: Box
    page_index: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> CalloutLabel:
        return cls(value=d["value"], box=tuple(d["box"]), page_index=d.get("page_index", 0))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class AssociationLabel:
    """The correct mention→callout association status for a text numeral mention."""

    entry_ordinal: int
    value: str
    status: str  # "verified" | "probable" | "ambiguous" | "unresolved"

    @classmethod
    def from_dict(cls, d: dict) -> AssociationLabel:
        return cls(entry_ordinal=d["entry_ordinal"], value=d["value"], status=d["status"])


@dataclass(frozen=True, slots=True)
class DocumentLabels:
    """Ground truth for one corpus document."""

    doc_id: str
    doc_type: str  # "grant" | "application"
    spec_page_indices: tuple[int, ...] = ()
    references: tuple[ReferenceLabel, ...] = ()
    callouts: tuple[CalloutLabel, ...] = ()
    associations: tuple[AssociationLabel, ...] = ()
    # A wrong-document fixture: the provider clean text is from a DIFFERENT patent,
    # so a correct engine substitutes nothing (100% rejection, §19.2).
    wrong_document: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> DocumentLabels:
        return cls(
            doc_id=d["doc_id"],
            doc_type=d["doc_type"],
            spec_page_indices=tuple(d.get("spec_page_indices", [])),
            references=tuple(ReferenceLabel.from_dict(r) for r in d.get("references", [])),
            callouts=tuple(CalloutLabel.from_dict(c) for c in d.get("callouts", [])),
            associations=tuple(AssociationLabel.from_dict(a) for a in d.get("associations", [])),
            wrong_document=d.get("wrong_document", False),
            notes=d.get("notes", ""),
        )


def index_references(labels: DocumentLabels) -> dict[int, ReferenceLabel]:
    """Reference labels keyed by the entry ordinal they describe."""
    return {r.ordinal: r for r in labels.references}


def load_labels_file(path: str | Path) -> DocumentLabels:
    """Load one hand-labeled corpus document from a JSON file (see corpus/README.md)."""
    return DocumentLabels.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def load_labels_dir(path: str | Path) -> list[DocumentLabels]:
    """Load every ``*.json`` label file in a corpus directory, sorted by name."""
    return [load_labels_file(p) for p in sorted(Path(path).glob("*.json"))]
