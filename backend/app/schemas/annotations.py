"""Annotation wire schemas (DESIGN.md §14, §16.6)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnnotationCreate(BaseModel):
    target_entry_id: str = Field(min_length=1, max_length=128)
    note: str = Field(min_length=1, max_length=4000)


class AnnotationUpdate(BaseModel):
    note: str = Field(min_length=1, max_length=4000)


class AnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    target_entry_id: str
    note: str
    created_at: datetime
