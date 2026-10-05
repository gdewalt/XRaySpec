"""Document wire schemas (DESIGN.md §14). API shape, distinct from ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    """Create a document. The actual source blob is attached later by the upload
    finalizer / fetch pipeline (§11); this slice records the document in
    ``preparing`` state."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    source_type: Literal["upload", "fetch"] = "upload"
    patent_identifier: str | None = Field(default=None, max_length=64)
    filename: str | None = Field(default=None, max_length=500)


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    patent_number: str | None = None
    workspace_id: str | None = None
    state: str
    active_artifact_id: str | None = None
    last_opened_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentRead]
