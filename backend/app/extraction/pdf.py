"""PDF -> abstract page model adapter (DESIGN.md §12.1, §25.2).

The only place the extraction engine touches a PDF library. Kept thin so the
geometry algorithm stays library-agnostic and unit-testable. Runs only inside the
isolated worker (§17.2); pdfplumber is imported lazily so the app package imports
without it.
"""

from __future__ import annotations

import io
import re
from dataclasses import replace
from statistics import median

from .model import Page, Word

_BOLD_FONT = re.compile(r"(?:bold|black|heavy|semibold|demi)", re.IGNORECASE)
_NUMERIC_TOKEN = re.compile(r"^\d{2,4}[A-Za-z]?$", re.IGNORECASE)
_ALPHA_TOKEN = re.compile(r"^[A-Za-z]{3,}$")


def _font_is_bold(font_name: object) -> bool | None:
    """Translate embedded PDF font names into conservative weight evidence."""
    if not isinstance(font_name, str) or not font_name.strip():
        return None
    return bool(_BOLD_FONT.search(font_name))


def _ink_density(image, word: Word) -> float:
    """Dark-pixel share inside a native word box on the rendered source page."""
    left = max(0, min(image.width - 1, round(word.x0 * image.width)))
    top = max(0, min(image.height - 1, round(word.y0 * image.height)))
    right = max(left + 1, min(image.width, round(word.x1 * image.width)))
    bottom = max(top + 1, min(image.height, round(word.y1 * image.height)))
    crop = image.crop((left, top, right, bottom))
    histogram = crop.histogram()[:256]
    return sum(histogram[:180]) / max(1, crop.width * crop.height)


def _mark_visually_bold_numbers(words: list[Word], image) -> list[Word]:
    """Detect bold numerals when OCR-backed PDFs expose only a generic font name.

    Reference numerals are compared with neighboring prose on the same printed
    line. Requiring both an absolute ink floor and a local density increase keeps
    thin line/page numbers from being promoted.
    """
    from .native import group_lines

    gray = image.convert("L")
    visually_bold: set[int] = set()
    for line in group_lines(words):
        prose = [word for word in line.words if _ALPHA_TOKEN.fullmatch(word.text.strip())]
        candidates = [
            word
            for word in line.words
            if _NUMERIC_TOKEN.fullmatch(word.text.strip().strip(".,;:()[]{}"))
        ]
        if not prose or not candidates:
            continue
        baseline = median(_ink_density(gray, word) for word in prose)
        threshold = max(0.13, baseline * 1.22)
        visually_bold.update(
            id(word) for word in candidates if _ink_density(gray, word) >= threshold
        )
    return [
        replace(word, is_bold=True) if id(word) in visually_bold else word
        for word in words
    ]


def load_pages(pdf_bytes: bytes) -> list[Page]:
    """Parse native words with normalized top-left coordinates."""
    import pdfplumber
    import pypdfium2 as pdfium

    pages: list[Page] = []
    rendered = pdfium.PdfDocument(pdf_bytes)
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for index, page in enumerate(pdf.pages):
                width = float(page.width) or 1.0
                height = float(page.height) or 1.0
                extracted = page.extract_words(
                    use_text_flow=False,
                    keep_blank_chars=False,
                    extra_attrs=["fontname"],
                )
                words = [
                    Word(
                        text=w["text"],
                        x0=w["x0"] / width,
                        y0=w["top"] / height,
                        x1=w["x1"] / width,
                        y1=w["bottom"] / height,
                        is_bold=_font_is_bold(w.get("fontname")),
                    )
                    for w in extracted
                ]
                if words:
                    from .callouts import is_drawing_page

                    has_numeric_candidates = any(
                        _NUMERIC_TOKEN.fullmatch(word.text.strip().strip(".,;:()[]{}"))
                        for word in words
                    )
                    if has_numeric_candidates and not is_drawing_page(words):
                        bitmap = rendered[index].render(scale=2.0)
                        words = _mark_visually_bold_numbers(words, bitmap.to_pil())
                pages.append(Page(index=index, words=words))
    finally:
        rendered.close()
    return pages
