"""Portable import: analyze in quarantine, then commit (DESIGN.md §11.3, §14.1).

``POST /imports`` validates and normalizes the untrusted ``.patent-viewer.json``
into a quarantined payload (no server identity restored, all file IDs regenerated)
and returns an analysis. ``POST /imports/{id}/commit`` applies explicit choices
(migration confirmation) and materializes an ``imported_unverified``, text-only
document with an immutable artifact and its bookmarks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from ...config import get_settings
from ...db.models import (
    Bookmark,
    ExtractionArtifact,
    Import,
    SourceDocument,
    UserDocument,
    new_id,
)
from ...imports import ImportLimits, ImportValidationError, analyze_import
from ...schemas.documents import DocumentRead
from ...schemas.imports import ImportAnalysis, ImportCommit
from ...services.audit import record_audit
from ..deps import CurrentUser, DbSession, Storage

router = APIRouter(tags=["imports"])


@router.post("/imports", response_model=ImportAnalysis, status_code=status.HTTP_201_CREATED)
async def analyze_portable_import(
    request: Request, user: CurrentUser, session: DbSession, store: Storage
) -> ImportAnalysis:
    settings = get_settings()

    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > settings.max_import_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Import file is too large")

    raw = await request.body()
    limits = ImportLimits(max_bytes=settings.max_import_bytes)
    try:
        parsed = analyze_import(raw, limits)
    except ImportValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{exc.reason}: {exc.message}") from None

    import_id = new_id("imp")
    object_key = f"imports/{user.id}/{import_id}.json"
    await store.write(
        object_key, json.dumps(parsed.payload()).encode("utf-8"), content_type="application/json"
    )

    row = Import(
        id=import_id,
        owner_id=user.id,
        object_key=object_key,
        schema_version=parsed.schema_version,
        needs_migration=parsed.needs_migration,
        doc_type=parsed.doc_type,
        title=parsed.title,
        entry_count=len(parsed.entries),
        bookmark_count=len(parsed.bookmarks),
        status="analyzed",
        warnings=parsed.warnings,
    )
    session.add(row)
    record_audit(
        session, actor_user_id=user.id, action="import.analyze",
        resource_class="import", resource_id=import_id,
    )
    await session.commit()

    return ImportAnalysis(
        import_id=import_id,
        schema_version=parsed.schema_version,
        needs_migration=parsed.needs_migration,
        doc_type=parsed.doc_type,
        title=parsed.title,
        entry_count=len(parsed.entries),
        bookmark_count=len(parsed.bookmarks),
        warnings=parsed.warnings,
    )


@router.post(
    "/imports/{import_id}/commit",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def commit_import(
    import_id: str, body: ImportCommit, user: CurrentUser, session: DbSession, store: Storage
) -> DocumentRead:
    imp = await session.scalar(
        select(Import).where(Import.id == import_id, Import.owner_id == user.id)
    )
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    if imp.status != "analyzed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Import already committed")
    if imp.needs_migration and not body.confirm_migration:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "migration_confirmation_required: set confirm_migration for a legacy v1 import",
        )

    payload = json.loads(await store.read(imp.object_key, limit=get_settings().max_import_bytes))

    source = SourceDocument(
        owner_id=user.id,
        source_type="import",
        doc_type=imp.doc_type,
        sha256=payload.get("source_sha256"),
        state="imported_unverified",
    )
    session.add(source)
    await session.flush()

    artifact = ExtractionArtifact(
        source_id=source.id,
        schema_version=2,
        disposition="imported",
        manifest_object_key=imp.object_key,
        is_active=True,
        ready_at=datetime.now(UTC),
    )
    session.add(artifact)
    await session.flush()

    doc = UserDocument(
        owner_id=user.id,
        source_id=source.id,
        title=body.title or imp.title or "Imported document",
        active_artifact_id=artifact.id,
        state="text_only",
    )
    session.add(doc)
    await session.flush()

    for bm in payload.get("bookmarks", []):
        session.add(
            Bookmark(
                owner_id=user.id,
                document_id=doc.id,
                entry_id=bm["entry_id"],
                label=bm.get("label"),
                color=bm.get("color"),
            )
        )

    imp.status = "committed"
    record_audit(
        session, actor_user_id=user.id, action="import.commit",
        resource_class="user_document", resource_id=doc.id,
    )
    await session.commit()
    await session.refresh(doc)
    return DocumentRead.model_validate(doc)
