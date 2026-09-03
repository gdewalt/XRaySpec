"""Job wire schemas (DESIGN.md §10.2, §14.1)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobRead(BaseModel):
    """Authoritative job snapshot (DESIGN.md §10.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str | None = None
    status: str
    stage: str | None = None
    stage_label: str | None = None
    attempt: int
    completed_units: int
    total_units: int | None = None
    unit: str | None = None
    overall_fraction: float | None = None
    indeterminate: bool
    cancel_requested: bool
    failure_code: str | None = None
    progress_sequence: int = 0
    created_at: datetime
    finished_at: datetime | None = None
