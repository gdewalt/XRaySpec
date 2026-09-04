"""Bootstrap a corpus label file from an extraction run (DESIGN.md §19.1).

Hand-labeling from scratch is impractical: entry ordinals and normalized boxes
would have to be transcribed off the engine's output. Instead, run the engine and
emit a *pre-filled* ``corpus/<doc_id>.json`` — every line's ordinal, its guessed
``col:line``/paragraph, and its box — that a human then **corrects in place**
(flip wrong locators, delete hallucinated lines, add missed ones, tag callouts and
associations). See ``corpus/LABELING.md``.

The pre-filled values are the engine's *guesses*, not ground truth, so a scaffold
must be reviewed before it counts as a label — the ``_scaffold.verified`` flag
records that. ``_scaffold`` (and any other unknown keys) are ignored by
``DocumentLabels.from_dict``, so a scaffold already loads and scores; reviewing is
what makes the score meaningful.

``build_scaffold`` is pure over an :class:`Artifact` so it is unit-testable without
the PDF/OCR libraries; the CLI wires the real extraction engine on top.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ..extraction.artifact import Artifact


def _box(box: Any) -> list[float] | None:
    return [round(float(v), 4) for v in box] if box else None


def _sample(items: list, n: int | None) -> list:
    """An evenly spaced subset of ``items`` (all of them when ``n`` is None/large)."""
    if n is None or n >= len(items) or n <= 0:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def build_scaffold(
    artifact: Artifact,
    *,
    doc_id: str,
    doc_type: str = "auto",
    sample: int | None = None,
    source_sha256: str | None = None,
) -> dict:
    """Map an extraction artifact to a pre-filled corpus label dict (§19.1)."""
    resolved_type = artifact.doc_type if doc_type == "auto" else doc_type
    ordinal_of = {e.entry_id: e.ordinal for e in artifact.entries}

    references: list[dict] = []
    for e in _sample(list(artifact.entries), sample):
        ref: dict[str, Any] = {"ordinal": e.ordinal}
        if resolved_type == "application":
            ref["paragraph"] = getattr(e.locator, "paragraph", None)
        else:
            ref["column"] = getattr(e.locator, "column", None)
            ref["printed_line"] = getattr(e.locator, "printed_line", None)
        box = _box(e.box)
        if box is not None:
            ref["box"] = box
        references.append(ref)

    callouts = [
        {"value": c.value, "box": _box(c.box), "page_index": c.page_index}
        for c in artifact.callout_occurrences
    ]
    associations = [
        {"entry_ordinal": ordinal_of.get(a.entry_id), "value": a.value, "status": a.status}
        for a in artifact.mention_associations
        if a.entry_id in ordinal_of
    ]
    spec_page_indices = sorted({e.page_index for e in artifact.entries})

    return {
        "doc_id": doc_id,
        "doc_type": resolved_type,
        "spec_page_indices": spec_page_indices,
        "references": references,
        "callouts": callouts,
        "associations": associations,
        "wrong_document": False,
        "notes": "",
        "_scaffold": {
            "generated_by": "app.eval.scaffold",
            "engine_version": artifact.engine_version,
            "config_hash": artifact.config_hash,
            "source_sha256": source_sha256 or artifact.source_sha256,
            "verified": False,
            "instructions": (
                "Engine GUESSES, not ground truth. Review every field, then set "
                "verified=true. See corpus/LABELING.md."
            ),
            "counts": {
                "references": len(references),
                "callouts": len(callouts),
                "associations": len(associations),
            },
        },
    }


def _write(scaffold: dict, out_dir: Path, *, force: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{scaffold['doc_id']}.json"
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists (use --force to overwrite)")
    path.write_text(json.dumps(scaffold, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.eval.scaffold",
        description="Pre-fill a corpus label file from an extraction run (DESIGN.md §19.1).",
    )
    parser.add_argument("pdf", type=Path, help="path to the source PDF")
    parser.add_argument("--doc-id", required=True, help="stable id, e.g. US12262260B2")
    parser.add_argument(
        "--doc-type", default="auto", choices=["auto", "grant", "application"]
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="keep only N evenly spaced reference lines (default: all)",
    )
    parser.add_argument("--out", type=Path, default=Path("corpus"), help="output directory")
    parser.add_argument("--force", action="store_true", help="overwrite an existing label file")
    args = parser.parse_args(argv)

    pdf_bytes = args.pdf.read_bytes()
    source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    from ..extraction.config import DEFAULT_CONFIG
    from ..extraction.core import extract

    artifact = extract(pdf_bytes, DEFAULT_CONFIG, args.doc_type)
    scaffold = build_scaffold(
        artifact, doc_id=args.doc_id, doc_type=args.doc_type,
        sample=args.sample, source_sha256=source_sha256,
    )
    path = _write(scaffold, args.out, force=args.force)

    counts = scaffold["_scaffold"]["counts"]
    print(f"Wrote {path}")
    print(
        f"  doc_type={scaffold['doc_type']}  pages={scaffold['spec_page_indices']}  "
        f"references={counts['references']}  callouts={counts['callouts']}  "
        f"associations={counts['associations']}"
    )
    print("  Review it against corpus/LABELING.md, then set _scaffold.verified=true.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
