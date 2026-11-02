"""Account settings wire schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AiUsageRead(BaseModel):
    requests: int = 0
    scans: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    last_used_at: datetime | None = None


class AccountRead(BaseModel):
    email: str | None = None
    uspto_api_key_configured: bool
    ai_scan_available: bool = False
    ai_usage: AiUsageRead = Field(default_factory=AiUsageRead)


class UsptoApiKeyUpdate(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096)
