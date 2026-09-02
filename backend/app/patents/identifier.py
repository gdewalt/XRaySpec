"""Structured US patent identifier parsing (DESIGN.md §7.2, §11.2).

Turns human or canonical input into a typed identity, distinguishing **grants**
(printed ``col:line``) from **application publications** (paragraph numbers),
which use different numbering systems. Ambiguous or unsupported input raises a
``PatentParseError`` (a validation choice) rather than guessing a fetch.

Supported at launch: granted utility patents (``B1``, ``B2``, and the pre-2001
``A`` grant) and application publications (``A1``, ``A2``, ``A9``). Design (``S``)
and plant (``P``) patents are out of scope.

Pure and framework-free so it is trivially unit-testable and reusable by the
fetch adapter, the ingestion classifier, and import validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

GRANT_KINDS = {"A", "B1", "B2"}
APPLICATION_KINDS = {"A1", "A2", "A9"}
_UNSUPPORTED_KINDS = {"S", "P", "P1", "P2", "P3", "E", "H"}

_CLEAN_WORDS = re.compile(r"PATENT|PUB(?:LICATION)?|APP(?:LICATION)?|NO\.?|#", re.IGNORECASE)
_COUNTRY_PREFIX = re.compile(r"^U\.?\s*S\.?")
_NUMBER_KIND = re.compile(r"^(\d+)([A-Z]\d?)?$")


class PatentParseError(ValueError):
    """Raised for input that cannot be resolved to a supported identity.

    ``reason`` is a stable slug (``empty``, ``unrecognized``, ``unsupported_kind``,
    ``ambiguous``, ``invalid_number``) for stable API error mapping.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class PatentIdentity:
    doc_type: Literal["grant", "application"]
    country: str
    number: str
    kind: str | None
    canonical: str
    display: str


def _grouped(number: str) -> str:
    return f"{int(number):,}"


def parse_patent_identifier(raw: str) -> PatentIdentity:
    if raw is None or not raw.strip():
        raise PatentParseError("empty", "No patent identifier was provided.")

    s = raw.strip().upper()
    had_slash = "/" in s
    s = _COUNTRY_PREFIX.sub("", s, count=1)
    s = _CLEAN_WORDS.sub("", s)
    s = re.sub(r"[\s,./]", "", s)

    m = _NUMBER_KIND.match(s)
    if not m:
        raise PatentParseError(
            "unrecognized", "Could not read a US patent number from that input."
        )
    number, kind = m.group(1), m.group(2)

    if kind in _UNSUPPORTED_KINDS:
        raise PatentParseError(
            "unsupported_kind", f"Kind code {kind} (design/plant/other) is not supported."
        )

    # Application publication
    if kind in APPLICATION_KINDS or (kind is None and had_slash):
        kind = kind or "A1"
        if len(number) != 11:
            raise PatentParseError(
                "invalid_number",
                "A US application publication number is 11 digits (YYYY + 7-digit serial).",
            )
        year, serial = number[:4], number[4:]
        return PatentIdentity(
            doc_type="application",
            country="US",
            number=number,
            kind=kind,
            canonical=f"US{number}{kind}",
            display=f"US {year}/{serial} {kind}",
        )

    # Grant (explicit grant kind, or a bare number)
    if kind in GRANT_KINDS or kind is None:
        if not 4 <= len(number) <= 8:
            raise PatentParseError(
                "ambiguous",
                "Enter a granted patent number (up to 8 digits) with an optional B1/B2 "
                "kind, or a YYYY/####### application publication number.",
            )
        canonical = f"US{number}{kind}" if kind else f"US{number}"
        display = f"US {_grouped(number)} {kind}" if kind else f"US {_grouped(number)}"
        return PatentIdentity(
            doc_type="grant", country="US", number=number, kind=kind,
            canonical=canonical, display=display,
        )

    raise PatentParseError("unsupported_kind", f"Kind code {kind} is not supported.")
