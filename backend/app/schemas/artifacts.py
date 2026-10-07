"""Artifact wire schemas (DESIGN.md §14.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    schema_version: int
    disposition: str | None = None
    is_active: bool
    ready_at: datetime | None = None


class ArtifactEntriesResponse(BaseModel):
    """The reading payload: entries + the mention indices for inline links.

    Entries/mentions are passed through as JSON objects (their shapes are the
    extraction domain model); the frontend has matching TypeScript types.
    """

    artifact_id: str
    doc_type: str | None = None
    mode: str | None = None
    disposition: str | None = None
    page_count: int | None = None
    warnings: list[str] = []
    front_matter: dict[str, Any] | None = None
    entries: list[dict[str, Any]]
    figure_mentions: list[dict[str, Any]]
    numeral_mentions: list[dict[str, Any]]
    figure_occurrences: list[dict[str, Any]]
    mention_associations: list[dict[str, Any]]
    callout_occurrences: list[dict[str, Any]]
