"""Parse authoritative specification text from USPTO PPUBS HTML."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .google_text import ProviderText

_NUMBERED_PARAGRAPH = re.compile(r"^\s*\((\d{1,4})\)\s*")
_SPACE = re.compile(r"\s+")
_CANONICAL = re.compile(r"\b(US)-?(\d+)-?([A-Z]\d?)\b", re.IGNORECASE)
_SECTIONS = {
    "abstract": "abstract",
    "background/summary": "description",
    "description": "description",
    "claims": "claims",
}


class _PpubsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_text: list[str] = []
        self.document_title: list[str] = []
        self._capture_title = False
        self._capture_h2 = False
        self._capture_h3 = False
        self._heading: list[str] = []
        self._section: str | None = None
        self._in_paragraph = False
        self._segment: list[str] = []
        self._block: list[tuple[str, bool]] = []
        self.sections: dict[str, list[str]] = {
            "abstract": [],
            "description": [],
            "claims": [],
        }

    def handle_starttag(self, tag: str, attrs) -> None:
        del attrs
        if tag == "title":
            self._capture_title = True
        elif tag == "h2" and not self.document_title:
            self._capture_h2 = True
        elif tag == "h3":
            self._capture_h3 = True
            self._heading = []
        elif tag == "p" and self._section:
            self._in_paragraph = True
            self._segment = []
            self._block = []
        elif tag == "br" and self._in_paragraph:
            self._finish_segment()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._capture_title = False
        elif tag == "h2":
            self._capture_h2 = False
        elif tag == "h3":
            heading = _SPACE.sub(" ", "".join(self._heading)).strip().casefold()
            self._section = _SECTIONS.get(heading)
            self._capture_h3 = False
        elif tag == "p" and self._in_paragraph:
            self._finish_segment()
            self._finish_block()
            self._in_paragraph = False

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self.title_text.append(data)
        if self._capture_h2:
            self.document_title.append(data)
        if self._capture_h3:
            self._heading.append(data)
        if self._in_paragraph:
            self._segment.append(data)

    def _finish_segment(self) -> None:
        text = _SPACE.sub(" ", "".join(self._segment)).strip()
        self._segment = []
        if not text:
            return
        marker = _NUMBERED_PARAGRAPH.match(text)
        if marker:
            text = text[marker.end() :].strip()
        if text:
            self._block.append((text, marker is not None))

    def _finish_block(self) -> None:
        if not self._section or not self._block:
            return
        for text, starts_numbered_paragraph in self._block:
            paragraphs = self.sections[self._section]
            if starts_numbered_paragraph or not paragraphs:
                paragraphs.append(text)
            else:
                paragraphs[-1] = f"{paragraphs[-1]} {text}".strip()


def extract_ppubs_text(page_html: str) -> ProviderText:
    """Return marker-free text while retaining PPUBS paragraph boundaries as newlines."""
    parser = _PpubsParser()
    parser.feed(page_html)
    raw_title = _SPACE.sub(" ", "".join(parser.title_text)).strip()
    canonical_match = _CANONICAL.search(raw_title)
    canonical = "".join(canonical_match.groups()).upper() if canonical_match else None
    title = _SPACE.sub(" ", "".join(parser.document_title)).strip() or None
    return ProviderText(
        canonical=canonical,
        title=title,
        description="\n".join(parser.sections["description"]),
        claims="\n".join(parser.sections["claims"]),
        abstract=" ".join(parser.sections["abstract"]),
    )
