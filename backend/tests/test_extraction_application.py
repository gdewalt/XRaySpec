"""Application-publication paragraph reconstruction (DESIGN.md §12.5)."""

from __future__ import annotations

from app.extraction.config import DEFAULT_CONFIG
from app.extraction.core import detect_doc_type, extract_from_pages
from app.extraction.locator import ApplicationLocator
from app.extraction.model import Page, Word


def _row(i: int, tokens: list[str]) -> list[Word]:
    cy = 0.10 + 0.04 * i
    y0, y1 = cy - 0.008, cy + 0.008
    words: list[Word] = []
    x = 0.10
    for tok in tokens:
        w = 0.02 * max(len(tok), 1)
        words.append(Word(tok, x, y0, x + w, y1))
        x += w + 0.01
    return words


def _application_page() -> Page:
    rows = [
        ["[0001]", "The", "invention", "relates", "to"],
        ["wireless", "communication", "systems."],
        ["[0002]", "In", "particular", "the"],
        ["system", "provides", "a", "method."],
    ]
    words: list[Word] = []
    for i, toks in enumerate(rows):
        words.extend(_row(i, toks))
    return Page(0, words)


def test_paragraph_extraction():
    art = extract_from_pages(
        [_application_page()], DEFAULT_CONFIG, source_sha256="x", doc_type="application"
    )
    assert art.doc_type == "application"
    assert isinstance(art.entries[0].locator, ApplicationLocator)

    tagged = [(e.locator.paragraph, e.source_text) for e in art.entries]
    assert tagged[0] == ("0001", "The invention relates to")  # marker stripped
    assert tagged[1][0] == "0001"  # continuation keeps the paragraph
    assert tagged[2] == ("0002", "In particular the")
    assert tagged[3][0] == "0002"

    # First line of a paragraph is 'detected'; continuations are 'interpolated'.
    assert art.entries[0].provenance.reference_method == "detected"
    assert art.entries[1].provenance.reference_method == "interpolated"


def test_paragraph_persists_across_pages():
    page2 = Page(1, _row(0, ["continues", "onto", "the", "next", "page."]))
    art = extract_from_pages(
        [_application_page(), page2], DEFAULT_CONFIG, source_sha256="x", doc_type="application"
    )
    # The last paragraph (0002) carries over to page 2's first line.
    assert art.entries[-1].locator.paragraph == "0002"


def test_detect_doc_type():
    app_words = [
        Word(f"[{i:04d}]", 0.10, 0.10 + 0.03 * i, 0.16, 0.11 + 0.03 * i) for i in range(1, 6)
    ]
    assert detect_doc_type([Page(0, app_words)]) == "application"

    grant_words = [Word("5", 0.03, 0.10, 0.06, 0.11), Word("The", 0.12, 0.10, 0.20, 0.11)]
    assert detect_doc_type([Page(0, grant_words)]) == "grant"
