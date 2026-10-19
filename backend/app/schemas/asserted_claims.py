"""Asserted-claim wire schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AssertedClaimUpsert(BaseModel):
    entry_id: str = Field(min_length=1, max_length=128)
    claim_number: int = Field(ge=1, le=100_000)


class AssertedClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    entry_id: str
    claim_number: int
    created_at: datetime
