"""Fetch error taxonomy (DESIGN.md §10.8, §11.2)."""

from __future__ import annotations


class FetchError(Exception):
    """A restricted-fetch failure. ``code`` is a stable slug for classification."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
