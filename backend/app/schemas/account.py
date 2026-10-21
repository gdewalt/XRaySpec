"""Account settings wire schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AccountRead(BaseModel):
    email: str | None = None
    uspto_api_key_configured: bool


class UsptoApiKeyUpdate(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096)
