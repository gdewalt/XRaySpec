"""External enrichment: clean-text alignment against the provider (DESIGN.md §13).

Optional and independently retryable — a provider/parse failure completes the core
artifact without enrichment (graceful degradation, §5, §13.3).
"""

from .align import (
    align_entries,
    enrich_from_page_html,
    enrich_from_ppubs_html,
    repair_serialized_display_overlaps,
    strip_leading_line_overlap,
)
from .google_text import ProviderText, extract_provider_text
from .identity import IdentityResult, verify_identity
from .ppubs_text import extract_ppubs_text

__all__ = [
    "align_entries",
    "enrich_from_page_html",
    "enrich_from_ppubs_html",
    "extract_provider_text",
    "extract_ppubs_text",
    "repair_serialized_display_overlaps",
    "strip_leading_line_overlap",
    "ProviderText",
    "verify_identity",
    "IdentityResult",
]
