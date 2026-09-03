"""Restricted-egress fetch adapter (DESIGN.md §11.2, §17.4).

Only the enrichment/fetch worker uses this. The SSRF guard runs on every hop.
"""

from .adapter import FetchResult, RestrictedFetcher
from .errors import FetchError
from .google_patents import extract_pdf_url, patent_page_url

__all__ = [
    "RestrictedFetcher",
    "FetchResult",
    "FetchError",
    "patent_page_url",
    "extract_pdf_url",
]
