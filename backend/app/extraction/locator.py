"""Typed citation locators (DESIGN.md §8.2).

A locator is the *discriminated* address of a line within its document. Grants
address text by printed ``column:line``; application publications address text by
paragraph number (``[0042]``). Everything downstream — citation rendering, deep
links, bookmarks — reads the typed locator; the rendered ``ref`` string is only a
view of it, never the primitive.

Kept as plain frozen dataclasses so the extraction core has no framework
dependency (§25.3.1). The API/wire representation lives separately in
``app.schemas``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union


@dataclass(frozen=True, slots=True)
class GrantLocator:
    """Printed column/line address on a granted patent."""

    column: int
    printed_line: int
    kind: Literal["grant"] = "grant"

    def render(self) -> str:
        """Human ``col:line`` form, e.g. ``3:15``."""
        return f"{self.column}:{self.printed_line}"


@dataclass(frozen=True, slots=True)
class ApplicationLocator:
    """Paragraph-number address on an application publication.

    ``paragraph`` is kept as a string so leading zeros are preserved
    (``"0042"`` renders as ``[0042]``).
    """

    paragraph: str
    kind: Literal["application"] = "application"

    def render(self) -> str:
        """Human paragraph form, e.g. ``[0042]``."""
        return f"[{self.paragraph}]"


Locator = Union[GrantLocator, ApplicationLocator]
"""Discriminated union; branch on ``.kind`` (``"grant"`` | ``"application"``)."""
