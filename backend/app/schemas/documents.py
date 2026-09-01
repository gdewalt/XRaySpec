"""Document wire schemas (DESIGN.md §14). API shape, distinct from ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    """Create a document. The actual source blob is attached later by the upload
    finalizer / fetch pipeline (§11); this slice records the document in
    ``preparing`` state."""

    title: str = Field(min_length=1, max_length=500)
    source_type: Literal["upload", "fetch"] = "upload"
    patent_identifier: Optional[str] = Field(default=None, max_length=64)
    filename: Optional[str] = Field(default=None, max_length=500)


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    state: str
    active_artifact_id: Optional[str] = None
    last_opened_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentRead]
