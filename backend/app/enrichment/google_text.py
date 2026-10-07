"""Google Patents clean-text and metadata extraction (DESIGN.md §13).

The parser is deliberately pure over already-fetched HTML. Besides making it
fixture-testable, this keeps all Google traffic behind the restricted fetcher.
Google's semantic paragraph containers are retained as newlines so they can be
used as a spacing fallback when USPTO PPUBS text is unavailable.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser

_SECTION = (
    r"<section\b(?=[^>]*\bitemprop\s*=\s*[\"']{prop}[\"'])[^>]*>"
    r"(.*?)</section>"
)
_DESC = re.compile(_SECTION.format(prop="description"), re.IGNORECASE | re.DOTALL)
_CLAIMS = re.compile(_SECTION.format(prop="claims"), re.IGNORECASE | re.DOTALL)
_ABSTRACT = re.compile(_SECTION.format(prop="abstract"), re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"}


@dataclass(frozen=True, slots=True)
class ProviderText:
    canonical: str | None
    title: str | None
    description: str
    claims: str
    abstract: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    @property
    def clean_text(self) -> str:
        # Newlines inside a section represent provider paragraphs. The space
        # between description and claims prevents that section boundary alone
        # from introducing a synthetic paragraph.
        return " ".join(p for p in (self.description, self.claims) if p)


def _strip(fragment: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


class _ParagraphParser(HTMLParser):
    """Collect semantic blocks without treating Google wrapper divs as paragraphs."""

    _SEMANTIC_CLASSES = {
        "description": {"description-line", "description-paragraph"},
        "claims": {"claim", "claim-text"},
        "abstract": {"abstract"},
    }

    def __init__(self, section: str) -> None:
        super().__init__(convert_charrefs=True)
        self.section = section
        self.depth = 0
        self.capture_depth: int | None = None
        self.buffer: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        if tag == "br":
            if self.capture_depth is not None:
                self.buffer.append(" ")
            return
        if tag not in _VOID_TAGS:
            self.depth += 1
        if self.capture_depth is not None:
            return

        values = {name.casefold(): (value or "") for name, value in attrs}
        classes = {part.casefold() for part in values.get("class", "").split()}
        semantic = bool(classes & self._SEMANTIC_CLASSES[self.section])
        if tag in {"p", "li"} or (tag == "div" and semantic):
            self.capture_depth = self.depth
            self.buffer = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self.capture_depth is not None and self.depth == self.capture_depth:
            text = _WS.sub(" ", "".join(self.buffer)).strip()
            if text:
                self.paragraphs.append(text)
            self.capture_depth = None
            self.buffer = []
        if tag not in _VOID_TAGS:
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data: str) -> None:
        if self.capture_depth is not None:
            self.buffer.append(data)


def _section_text(match: re.Match[str] | None, section: str) -> str:
    if match is None:
        return ""
    fragment = match.group(1)
    parser = _ParagraphParser(section)
    parser.feed(fragment)
    paragraphs = parser.paragraphs
    if not paragraphs:
        fallback = _strip(fragment)
        paragraphs = [fallback] if fallback else []
    return "\n".join(paragraphs)


class _MetadataParser(HTMLParser):
    _DATE_LABELS = {
        "priority": "Priority date",
        "filing": "Filing date",
        "publication": "Publication date",
        "issue": "Issue date",
        "datesubmitted": "Submitted date",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical: str | None = None
        self.title: str | None = None
        self.abstract: str = ""
        self.metadata: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() != "meta":
            return
        values = {name.casefold(): (value or "").strip() for name, value in attrs}
        name = values.get("name", "").casefold()
        content = values.get("content", "").strip()
        scheme = values.get("scheme", "").casefold()
        if not content:
            return

        if name == "citation_patent_number":
            self.canonical = content
        elif name in {"dc.title", "citation_title"} and self.title is None:
            self.title = content
        elif name == "dc.description" and not self.abstract:
            self.abstract = content

        label: str | None = None
        if name in {"dc.contributor", "citation_inventor", "citation_author"}:
            label = "Original assignee" if "assignee" in scheme else "Inventor"
        elif name == "citation_assignee":
            label = "Original assignee"
        elif name == "dc.date":
            label = self._DATE_LABELS.get(scheme)
        elif name == "citation_priority_date":
            label = "Priority date"
        elif name == "citation_filing_date":
            label = "Filing date"
        elif name == "citation_publication_date":
            label = "Publication date"
        if label and (label, content) not in self.metadata:
            self.metadata.append((label, content))


def extract_provider_text(page_html: str) -> ProviderText:
    """Parse clean text, paragraph boundaries, abstract, and basic patent metadata."""
    metadata = _MetadataParser()
    metadata.feed(page_html)
    abstract = _section_text(_ABSTRACT.search(page_html), "abstract") or metadata.abstract
    return ProviderText(
        canonical=metadata.canonical,
        title=metadata.title,
        description=_section_text(_DESC.search(page_html), "description"),
        claims=_section_text(_CLAIMS.search(page_html), "claims"),
        abstract=_WS.sub(" ", abstract).strip(),
        metadata=tuple(metadata.metadata),
    )
