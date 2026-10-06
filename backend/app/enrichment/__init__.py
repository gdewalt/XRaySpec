"""External enrichment: clean-text alignment against the provider (DESIGN.md §13).

Optional and independently retryable — a provider/parse failure completes the core
artifact without enrichment (graceful degradation, §5, §13.3).
"""

from .align import align_entries, enrich_from_page_html, enrich_from_ppubs_html
from .google_text import ProviderText, extract_provider_text
from .identity import IdentityResult, verify_identity
from .ppubs_text import extract_ppubs_text

__all__ = [
    "align_entries",
    "enrich_from_page_html",
    "enrich_from_ppubs_html",
    "extract_provider_text",
    "extract_ppubs_text",
    "ProviderText",
    "verify_identity",
    "IdentityResult",
]
