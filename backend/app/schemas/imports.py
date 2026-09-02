"""Portable-import wire schemas (DESIGN.md §11.3, §14.1)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImportAnalysis(BaseModel):
    """Result of analyzing an import in quarantine (before commit)."""

    import_id: str
    schema_version: int
    needs_migration: bool
    doc_type: str | None = None
    title: str | None = None
    entry_count: int
    bookmark_count: int
    imported_unverified: bool = True
    warnings: list[str]


class ImportCommit(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    confirm_migration: bool = False
