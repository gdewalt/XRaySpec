"""Load and score the real labeled corpus (DESIGN.md §19.1).

Discovers verified ``corpus/*.json`` label files and scores each against a fresh
extraction of its PDF, folding the results into the seed-corpus aggregate. Two
realities shape the design:

- Scoring a real document needs its extraction artifact, which needs the PDF and
  the OCR/PDF libraries — none of which live in the repo (PDFs are copyrighted and
  stored out of band). So the artifact is obtained through a **resolver** the caller
  supplies; the default extracts ``<doc_id>.pdf`` from a PDF directory and returns
  ``None`` (skip, not fail) when the PDF or the libraries are absent. In CI the
  corpus dir holds only the schema example, so nothing is scored and the seed
  corpus carries the gates.
- Only **verified** label files count. A raw scaffold (``_scaffold.verified`` false)
  is engine output, not ground truth, so it is skipped until a human reviews it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

from ..extraction.artifact import Artifact
from .labels import DocumentLabels
from .metrics import CaseMetrics, score_case

CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus"

# A resolver turns (doc_id, doc_type) into an extraction artifact, or None to skip.
ArtifactResolver = Callable[[str, str], Artifact | None]


def _is_verified(raw: dict) -> bool:
    """A hand-written file (no ``_scaffold``) counts; a scaffold counts only once
    its ``_scaffold.verified`` flag is set."""
    scaffold = raw.get("_scaffold")
    return True if scaffold is None else bool(scaffold.get("verified", False))


def iter_labeled(corpus_dir: Path = CORPUS_DIR) -> Iterator[tuple[str, DocumentLabels]]:
    """Yield ``(doc_id, labels)`` for each verified label file, skipping the schema
    example and unreviewed scaffolds."""
    for path in sorted(corpus_dir.glob("*.json")):
        if path.stem == "example":
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not _is_verified(raw):
            continue
        yield path.stem, DocumentLabels.from_dict(raw)


def extract_resolver(pdf_dir: str | Path) -> ArtifactResolver:
    """Resolver that extracts ``<pdf_dir>/<doc_id>.pdf`` with the real engine.

    Returns None (skip) when the PDF is missing or the extraction libraries are
    unavailable — so the harness runs everywhere, scoring whatever it can."""
    pdf_root = Path(pdf_dir)

    def resolve(doc_id: str, doc_type: str) -> Artifact | None:
        pdf = pdf_root / f"{doc_id}.pdf"
        if not pdf.exists():
            return None
        try:
            from ..extraction.config import DEFAULT_CONFIG
            from ..extraction.core import extract
        except ImportError:
            return None
        return extract(pdf.read_bytes(), DEFAULT_CONFIG, doc_type)

    return resolve


def score_corpus(
    resolver: ArtifactResolver, corpus_dir: Path = CORPUS_DIR
) -> tuple[list[CaseMetrics], list[tuple[str, str]]]:
    """Score every verified label file whose artifact the resolver can produce.

    Returns ``(metrics, skipped)`` where ``skipped`` is ``(doc_id, reason)`` for
    labels with no available artifact."""
    metrics: list[CaseMetrics] = []
    skipped: list[tuple[str, str]] = []
    for doc_id, labels in iter_labeled(corpus_dir):
        artifact = resolver(doc_id, labels.doc_type)
        if artifact is None:
            skipped.append((doc_id, "no artifact (PDF or extraction libs unavailable)"))
            continue
        metrics.append(score_case(artifact, labels))
    return metrics, skipped
