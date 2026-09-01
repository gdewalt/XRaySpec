"""ORM models — the durable domain (DESIGN.md §7).

Single-tenant: there is no ``Tenant``/``Membership``. Every resource is owned by
a ``User`` and authorization is an owner predicate.

Only ``User`` and ``ExtractionJob`` are sketched here to establish the pattern
(and because the job table *is* the queue, §10.3). The remaining entities from
§7 are added in Phase 1:

  SourceDocument, UserDocument, JobAttempt, StageCheckpoint, ExtractionArtifact,
  EnrichmentSnapshot, Bookmark, Annotation, ReferenceLinkOverride,
  CitationProfile, ExportSnapshot, AuditEvent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    subject: Mapped[str] = mapped_column(String, unique=True, index=True)  # Supabase sub
    email: Mapped[str | None] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractionJob(Base):
    """Rows in this table are the queue (claimed via SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "extraction_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String, default=None)
    # Lease/fencing fields (§10.4) — added with the worker in Phase 2.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
