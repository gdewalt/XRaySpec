"""Reference-link override wire schemas (DESIGN.md §7, §12.7)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OverrideUpsert(BaseModel):
    entry_id: str = Field(min_length=1, max_length=128)
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=0)
    callout_id: str | None = Field(default=None, max_length=128)


class OverrideRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    entry_id: str
    span_start: int
    span_end: int
    callout_id: str | None = None
    created_at: datetime
