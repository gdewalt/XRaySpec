"""Google Patents resolution (DESIGN.md §11.2).

The patent page carries a ``citation_pdf_url`` meta tag pointing at the PDF on
``patentimages.storage.googleapis.com``. Extraction is a pure function over HTML
so it can be fixture-tested; the fetch itself goes through the restricted adapter.
"""

from __future__ import annotations

import re

from .errors import FetchError

_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_IS_PDF_META = re.compile(r"""name\s*=\s*["']citation_pdf_url["']""", re.IGNORECASE)
_CONTENT = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def patent_page_url(canonical: str) -> str:
    return f"https://patents.google.com/patent/{canonical}/en"


def extract_pdf_url(html: str) -> str:
    for tag in _META_TAG.findall(html):
        if _IS_PDF_META.search(tag):
            match = _CONTENT.search(tag)
            if match:
                return match.group(1)
    raise FetchError("no_pdf_link", "no citation_pdf_url meta tag found on the patent page")
