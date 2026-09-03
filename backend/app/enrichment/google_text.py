"""Google Patents clean-text + metadata extraction (DESIGN.md §13).

Pure over the already-fetched patent-page HTML (the same page the PDF URL comes
from), so it is fixture-testable. Best-effort against Google Patents markup: the
description/claims live in ``itemprop`` sections, and the title/number in meta
tags. Verify against live HTML before relying in production; the alignment logic
that consumes this is provider-agnostic.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_SECTION = r'<section[^>]*itemprop="{prop}"[^>]*>(.*?)</section>'
_DESC = re.compile(_SECTION.format(prop="description"), re.IGNORECASE | re.DOTALL)
_CLAIMS = re.compile(_SECTION.format(prop="claims"), re.IGNORECASE | re.DOTALL)
_META = r'<meta[^>]*name=["\']{name}["\'][^>]*content=["\']([^"\']+)["\']'
_TITLE = re.compile(_META.format(name="DC.title"), re.IGNORECASE)
_NUMBER = re.compile(_META.format(name="citation_patent_number"), re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ProviderText:
    canonical: str | None
    title: str | None
    description: str
    claims: str

    @property
    def clean_text(self) -> str:
        return "\n".join(p for p in (self.description, self.claims) if p)


def _strip(fragment: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def extract_provider_text(page_html: str) -> ProviderText:
    desc = _DESC.search(page_html)
    claims = _CLAIMS.search(page_html)
    title = _TITLE.search(page_html)
    number = _NUMBER.search(page_html)
    return ProviderText(
        canonical=number.group(1) if number else None,
        title=html.unescape(title.group(1)) if title else None,
        description=_strip(desc.group(1)) if desc else "",
        claims=_strip(claims.group(1)) if claims else "",
    )
