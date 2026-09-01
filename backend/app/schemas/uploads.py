"""Upload/ingestion wire schemas (DESIGN.md §11.1, §14.1)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .documents import DocumentRead


class UploadCreate(BaseModel):
    filename: str | None = Field(default=None, max_length=500)
    content_type: str | None = Field(default=None, max_length=128)


class UploadGrantResponse(BaseModel):
    """Everything the browser needs to PUT the PDF directly to private storage."""

    upload_id: str
    object_key: str
    url: str
    method: str
    headers: dict[str, str]
    max_bytes: int
    expires_at: datetime


class UploadComplete(BaseModel):
    title: str | None = Field(default=None, max_length=500)


class UploadCompleteResponse(BaseModel):
    document: DocumentRead
    job_id: str
    job_status: str
