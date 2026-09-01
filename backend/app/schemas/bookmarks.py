"""Bookmark wire schemas (DESIGN.md §14, §16.6)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BookmarkCreate(BaseModel):
    entry_id: str = Field(min_length=1, max_length=128)
    label: Optional[str] = Field(default=None, max_length=200)
    color: Optional[str] = Field(default=None, max_length=32)


class BookmarkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    entry_id: str
    label: Optional[str] = None
    color: Optional[str] = None
    created_at: datetime
