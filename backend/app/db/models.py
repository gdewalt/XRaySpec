"""ORM models — the durable domain (DESIGN.md §7).

Single-tenant: there is no ``Tenant``/``Membership``. Every resource is owned by a
``User`` and authorization is an owner predicate applied on every query.

IDs are opaque, prefixed, full-entropy strings (DESIGN.md §14.2). Storage shape
lives here; the API (wire) shape lives in ``app.schemas``, kept separate.

Modeled in this slice: User, SourceDocument, UserDocument, ExtractionArtifact
(minimal), Bookmark, Annotation, AuditEvent, ExtractionJob. Remaining §7 entities
(JobAttempt, StageCheckpoint, EnrichmentSnapshot, ReferenceLinkOverride,
CitationProfile, ExportSnapshot) arrive with their phases.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("usr"))
    subject: Mapped[str] = mapped_column(String, unique=True, index=True)  # Supabase auth sub
    email: Mapped[str | None] = mapped_column(String, index=True, default=None)
    created_at: Mapped[datetime] = _ts()


class SourceDocument(Base):
    """Owned source PDF + its provenance (DESIGN.md §7). ``pdf_object_key`` is null
    until the upload finalizer attaches a verified blob (state ``preparing``)."""

    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("src"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_type: Mapped[str] = mapped_column(String)  # "upload" | "fetch"
    doc_type: Mapped[str | None] = mapped_column(String, default=None)  # "grant"|"application"
    pdf_object_key: Mapped[str | None] = mapped_column(String, default=None)
    sha256: Mapped[str | None] = mapped_column(String, default=None)
    byte_size: Mapped[int | None] = mapped_column(default=None)
    page_count: Mapped[int | None] = mapped_column(default=None)
    patent_canonical: Mapped[str | None] = mapped_column(String, default=None)
    original_filename: Mapped[str | None] = mapped_column(String, default=None)
    state: Mapped[str] = mapped_column(String, default="preparing")  # preparing|ready|failed
    created_at: Mapped[datetime] = _ts()


class ExtractionArtifact(Base):
    """Immutable extraction result (minimal here; expanded in Phase 3, §8).

    ``is_active`` marks the current version; ``is_rollback`` the retained prior
    one (DESIGN.md §9.2). No fuzzy multi-version history."""

    __tablename__ = "extraction_artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("art"))
    source_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), index=True)
    schema_version: Mapped[int] = mapped_column(default=2)
    disposition: Mapped[str | None] = mapped_column(String, default=None)
    manifest_object_key: Mapped[str | None] = mapped_column(String, default=None)
    is_active: Mapped[bool] = mapped_column(default=False)
    is_rollback: Mapped[bool] = mapped_column(default=False)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = _ts()


class Workspace(Base):
    """An owner-scoped patent grouping in the library."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("wsp"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_workspace_owner_name"),
    )


class UserDocument(Base):
    """The user-facing document (DESIGN.md §7). Points at one source and its
    currently active artifact."""

    __tablename__ = "user_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("doc"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id"), index=True, default=None
    )
    title: Mapped[str] = mapped_column(String)
    active_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_artifacts.id"), default=None
    )
    state: Mapped[str] = mapped_column(String, default="preparing", index=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = _ts()


class Upload(Base):
    """A single-purpose, expiring direct-upload grant (DESIGN.md §11.1).

    ``status`` enforces one-time use: a grant is ``pending`` until finalized, then
    ``completed``. ``sha256``/``byte_size`` are the server-observed values recorded
    at finalize."""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("upl"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    object_key: Mapped[str] = mapped_column(String)
    max_bytes: Mapped[int] = mapped_column()
    content_type: Mapped[str | None] = mapped_column(String, default=None)
    original_filename: Mapped[str | None] = mapped_column(String, default=None)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)  # pending|completed
    sha256: Mapped[str | None] = mapped_column(String, default=None)
    byte_size: Mapped[int | None] = mapped_column(default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class Import(Base):
    """A quarantined portable import awaiting an explicit commit (DESIGN.md §11.3).

    The normalized, validated payload is written to object storage; this row holds
    only the summary needed to analyze and commit. No IDs, keys, or trust claims
    from the file are ever persisted."""

    __tablename__ = "imports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("imp"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    object_key: Mapped[str] = mapped_column(String)  # normalized payload in object storage
    schema_version: Mapped[int] = mapped_column()
    needs_migration: Mapped[bool] = mapped_column(default=False)
    doc_type: Mapped[str | None] = mapped_column(String, default=None)
    title: Mapped[str | None] = mapped_column(String, default=None)
    entry_count: Mapped[int] = mapped_column(default=0)
    bookmark_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(  # analyzed | committed
        String, default="analyzed", index=True
    )
    warnings: Mapped[list | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = _ts()


class ExtractionJob(Base):
    """Rows in this table are the queue (claimed via ``SELECT ... FOR UPDATE SKIP
    LOCKED``, DESIGN.md §10.3). Lease/fencing columns arrive with the Phase 2
    worker."""

    __tablename__ = "extraction_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("job"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("user_documents.id"), default=None)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String, default=None)
    stage_label: Mapped[str | None] = mapped_column(String, default=None)

    # Lease + fencing (§10.4): a worker claims the job, renews a short lease with
    # heartbeats, and stamps every write with its fencing token so a stale worker
    # cannot publish or mutate current state.
    attempt: Mapped[int] = mapped_column(default=0)
    worker_id: Mapped[str | None] = mapped_column(String, default=None)
    fencing_token: Mapped[str | None] = mapped_column(String, default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Cancellation (§10.5) and durable progress snapshot (§10.2).
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    completed_units: Mapped[int] = mapped_column(default=0)
    total_units: Mapped[int | None] = mapped_column(default=None)
    unit: Mapped[str | None] = mapped_column(String, default=None)
    overall_fraction: Mapped[float | None] = mapped_column(Float, default=None)
    indeterminate: Mapped[bool] = mapped_column(default=True)

    # Durable, monotonically increasing progress sequence (§10.2). Bumped on every
    # meaningful transition; the SSE stream (`/jobs/{id}/events`) uses it as the
    # event id so a reconnect with `Last-Event-ID` skips what the client already saw.
    progress_sequence: Mapped[int] = mapped_column(default=0)

    failure_code: Mapped[str | None] = mapped_column(String, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = _ts()


class JobCheckpoint(Base):
    """Resume checkpoint (DESIGN.md §10.7). Only two kinds exist:

    - ``source_ready`` — the source has been fetched/validated/preflighted
      (page inventory, dimensions, classification inputs); ``page_index`` is null.
    - ``page_text`` — one page's text result (native or OCR) with word coordinates
      and confidence, keyed by ``page_index``.

    ``cache_key`` binds a checkpoint to the exact source hash + engine + config +
    artifact schema that produced it; a mismatch means the checkpoint is stale and
    is recomputed. Checkpoints are keyed by ``source_id`` so they are shared across
    a source's jobs (a resume reuses a prior attempt's pages) and purged with the
    document. The fast downstream pipeline is always recomputed, never checkpointed.
    """

    __tablename__ = "job_checkpoints"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ckpt"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), index=True)
    kind: Mapped[str] = mapped_column(String)  # "source_ready" | "page_text"
    page_index: Mapped[int | None] = mapped_column(default=None)
    cache_key: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("source_id", "kind", "page_index", name="uq_checkpoint_page"),
    )


class Bookmark(Base):
    """User bookmark targeting a stable artifact ``entry_id`` (DESIGN.md §7, §16.6)."""

    __tablename__ = "bookmarks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("bmk"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("user_documents.id"), index=True)
    entry_id: Mapped[str] = mapped_column(String)
    label: Mapped[str | None] = mapped_column(String, default=None)
    color: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = _ts()


class Annotation(Base):
    """User annotation targeting a stable artifact ``entry_id`` (DESIGN.md §7)."""

    __tablename__ = "annotations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ann"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("user_documents.id"), index=True)
    target_entry_id: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = _ts()


class PdfAnnotation(Base):
    """A user-created mark anchored to normalized coordinates on a PDF page."""

    __tablename__ = "pdf_annotations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pdfann"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("user_documents.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    page_index: Mapped[int] = mapped_column()
    geometry: Mapped[dict] = mapped_column(JSON)
    color: Mapped[str | None] = mapped_column(String, default=None)
    note: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = _ts()


class ReferenceLinkOverride(Base):
    """A user's correction of a mention→callout association (DESIGN.md §7, §12.7).

    Keyed by the mention (``entry_id`` + character span). ``callout_id`` is the
    chosen drawing callout, or null to record "no correct callout" (mark the
    mention unresolved). One override per mention; purged with the document."""

    __tablename__ = "reference_link_overrides"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rlo"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("user_documents.id"), index=True)
    entry_id: Mapped[str] = mapped_column(String)
    span_start: Mapped[int] = mapped_column()
    span_end: Mapped[int] = mapped_column()
    callout_id: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("document_id", "entry_id", "span_start", "span_end", name="uq_override"),
    )


class AuditEvent(Base):
    """Content-free audit record (DESIGN.md §7, §18.3). ``details`` must never
    contain document text or other sensitive content."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("aud"))
    actor_user_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    action: Mapped[str] = mapped_column(String)
    resource_class: Mapped[str] = mapped_column(String)
    resource_id: Mapped[str | None] = mapped_column(String, default=None)
    outcome: Mapped[str] = mapped_column(String, default="success")
    correlation_id: Mapped[str | None] = mapped_column(String, default=None)
    details: Mapped[dict | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = _ts()
