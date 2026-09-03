"""PDF -> abstract page model adapter (DESIGN.md §12.1, §25.2).

The only place the extraction engine touches a PDF library. Kept thin so the
geometry algorithm stays library-agnostic and unit-testable. Runs only inside the
isolated worker (§17.2); pdfplumber is imported lazily so the app package imports
without it.
"""

from __future__ import annotations

import io

from .model import Page, Word


def load_pages(pdf_bytes: bytes) -> list[Page]:
    """Parse native words with normalized top-left coordinates."""
    import pdfplumber

    pages: list[Page] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for index, page in enumerate(pdf.pages):
            width = float(page.width) or 1.0
            height = float(page.height) or 1.0
            words = [
                Word(
                    text=w["text"],
                    x0=w["x0"] / width,
                    y0=w["top"] / height,
                    x1=w["x1"] / width,
                    y1=w["bottom"] / height,
                )
                for w in page.extract_words(use_text_flow=False, keep_blank_chars=False)
            ]
            pages.append(Page(index=index, words=words))
    return pages
