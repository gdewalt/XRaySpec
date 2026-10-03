"""Workspace wire schemas for grouping patents in the library."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Workspace name cannot be blank")
        return normalized


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime


class WorkspaceList(BaseModel):
    items: list[WorkspaceRead]


class DocumentWorkspaceUpdate(BaseModel):
    workspace_id: str | None = None
