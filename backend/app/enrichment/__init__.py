"""External enrichment: clean-text alignment against the provider (DESIGN.md §13).

Optional and independently retryable — a provider/parse failure completes the core
artifact without enrichment (graceful degradation, §5, §13.3).
"""

from .align import align_entries, enrich_from_page_html
from .google_text import ProviderText, extract_provider_text
from .identity import IdentityResult, verify_identity

__all__ = [
    "align_entries",
    "enrich_from_page_html",
    "extract_provider_text",
    "ProviderText",
    "verify_identity",
    "IdentityResult",
]
