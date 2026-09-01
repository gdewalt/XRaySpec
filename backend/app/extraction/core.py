"""Clean-room extraction core (DESIGN.md §12, §25.1, §25.3.1).

The single seam between "a PDF" and "an immutable artifact". It is **pure**: no
web layer, no database, no object storage, no global state — just bytes and a
config in, an ``Artifact`` out. That purity is what makes it unit- and
golden-testable in isolation and safely runnable inside the sandboxed worker.

Paradigm: deterministic and geometry-anchored. Models may *propose* (later, as
confidence-gated hints), geometry must *confirm*, and ``source_text`` is never
overwritten by a model. See DESIGN.md §25.1.

Implementation lands incrementally:
  - Phase 2: grant ``col:line``, native + whole-page OCR.
  - Phase 3: hybrid page selection, application paragraph reconstruction,
    figure mapping, drawing-callout detection, reference-numeral association,
    and identity-verified clean-text alignment.
"""

from __future__ import annotations

from .artifact import Artifact
from .config import ExtractionConfig


def extract(pdf_bytes: bytes, config: ExtractionConfig) -> Artifact:
    """Extract an immutable :class:`Artifact` from raw PDF bytes.

    Deterministic: identical ``pdf_bytes`` + ``config`` yield a semantically
    equivalent artifact (excluding IDs/timestamps), per the determinism gate in
    DESIGN.md §19.2.
    """
    raise NotImplementedError(
        "Extraction core — implemented in Phase 2/3 (see DESIGN.md §12, §25.1)."
    )
