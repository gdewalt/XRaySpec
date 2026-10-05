"""Documents + bookmarks API (DESIGN.md §14.1, §16.6).

Every query applies an **owner predicate**: a resource that does not belong to
the caller is indistinguishable from one that does not exist (404), per §14.2.
This is the release gate in §22 ("every document endpoint proves resource
ownership").

This slice records documents in ``preparing`` state; the upload-grant / fetch
ingestion pipeline (§11) and full deletion workflow (§9.4) land in later slices.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from ...config import get_settings
from ...db.models import (
    Annotation,
    Bookmark,
    ExtractionJob,
    PdfAnnotation,
    ReferenceLinkOverride,
    SourceDocument,
    UserDocument,
)
from ...patents import PatentParseError, parse_patent_identifier
from ...schemas.annotations import AnnotationCreate, AnnotationRead, AnnotationUpdate
from ...schemas.bookmarks import BookmarkCreate, BookmarkRead
from ...schemas.documents import DocumentCreate, DocumentList, DocumentRead
from ...schemas.jobs import JobRead
from ...schemas.overrides import OverrideRead, OverrideUpsert
from ...schemas.pdf_annotations import PdfAnnotationCreate, PdfAnnotationRead
from ...services.audit import record_audit
from ...services.deletion import purge_document
from ..deps import CurrentUser, DbSession, Storage

router = APIRouter(tags=["documents"])


async def _owned_document(session, user, document_id: str) -> UserDocument:
    """Fetch a document owned by ``user`` or raise 404 (never 403 — §14.2)."""
    doc = await session.scalar(
        select(UserDocument).where(
            UserDocument.id == document_id,
            UserDocument.owner_id == user.id,
            UserDocument.state != "deleted",
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


@router.post("/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def create_document(
    body: DocumentCreate, user: CurrentUser, session: DbSession
) -> DocumentRead:
    """Create a document from a canonical patent identifier (fetch) or as a
    placeholder. Direct PDF uploads go through ``POST /uploads`` instead."""
    if body.source_type == "fetch":
        return await _create_fetch_document(body, user, session)
    return await _create_placeholder_document(body, user, session)


async def _create_fetch_document(
    body: DocumentCreate, user: CurrentUser, session: DbSession
) -> DocumentRead:
    if not body.patent_identifier:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "patent_identifier is required for a fetch source"
        )
    try:
        identity = parse_patent_identifier(body.patent_identifier)
    except PatentParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{exc.reason}: {exc.message}") from None

    source = SourceDocument(
        owner_id=user.id,
        source_type="fetch",
        doc_type=identity.doc_type,
        patent_canonical=identity.canonical,
        state="pending_fetch",
    )
    session.add(source)
    await session.flush()

    doc = UserDocument(
        owner_id=user.id,
        source_id=source.id,
        title=body.title or identity.display,
        state="processing",
    )
    session.add(doc)
    await session.flush()

    # The restricted-egress fetch worker (Phase 2) performs the actual download.
    job = ExtractionJob(
        owner_id=user.id, document_id=doc.id, status="queued", stage="fetching_source"
    )
    session.add(job)
    await session.flush()

    record_audit(
        session, actor_user_id=user.id, action="document.create",
        resource_class="user_document", resource_id=doc.id,
    )
    record_audit(
        session, actor_user_id=user.id, action="job.create",
        resource_class="extraction_job", resource_id=job.id,
    )
    await session.commit()
    await session.refresh(doc)
    return DocumentRead.model_validate(doc)


async def _create_placeholder_document(
    body: DocumentCreate, user: CurrentUser, session: DbSession
) -> DocumentRead:
    source = SourceDocument(
        owner_id=user.id,
        source_type=body.source_type,
        original_filename=body.filename,
        state="preparing",
    )
    session.add(source)
    await session.flush()

    title = body.title or body.filename or "Untitled document"
    doc = UserDocument(owner_id=user.id, source_id=source.id, title=title, state="preparing")
    session.add(doc)
    await session.flush()

    record_audit(
        session,
        actor_user_id=user.id,
        action="document.create",
        resource_class="user_document",
        resource_id=doc.id,
    )
    await session.commit()
    await session.refresh(doc)
    return DocumentRead.model_validate(doc)


@router.get("/documents/{document_id}/source.pdf")
async def get_source_pdf(
    document_id: str, user: CurrentUser, session: DbSession, store: Storage
) -> Response:
    """Authorized source-PDF delivery (DESIGN.md §14.1, §17.5). Storage keys never
    appear in responses; the bytes are streamed from private storage."""
    doc = await _owned_document(session, user, document_id)
    source = await session.get(SourceDocument, doc.source_id)
    if source is None or not source.pdf_object_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No source PDF for this document")
    data = await store.read(source.pdf_object_key, limit=get_settings().max_upload_bytes)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline", "Cache-Control": "private, max-age=300"},
    )


@router.get("/documents/{document_id}/job", response_model=JobRead)
async def get_document_job(
    document_id: str, user: CurrentUser, session: DbSession
) -> JobRead:
    """The document's most recent extraction job (DESIGN.md §10.2), so the UI can
    open its SSE progress stream. Owner-scoped via the document; 404 if the
    document isn't the caller's or has no job yet."""
    await _owned_document(session, user, document_id)
    job = await session.scalar(
        select(ExtractionJob)
        .where(ExtractionJob.document_id == document_id, ExtractionJob.owner_id == user.id)
        .order_by(ExtractionJob.created_at.desc())
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No job for this document")
    return JobRead.model_validate(job)


@router.get("/documents", response_model=DocumentList)
async def list_documents(user: CurrentUser, session: DbSession) -> DocumentList:
    rows = await session.scalars(
        select(UserDocument)
        .where(UserDocument.owner_id == user.id, UserDocument.state != "deleted")
        .order_by(UserDocument.created_at.desc())
    )
    return DocumentList(items=[DocumentRead.model_validate(d) for d in rows])


@router.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(document_id: str, user: CurrentUser, session: DbSession) -> DocumentRead:
    doc = await _owned_document(session, user, document_id)
    doc.last_opened_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(doc)
    return DocumentRead.model_validate(doc)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, user: CurrentUser, session: DbSession, store: Storage
) -> None:
    # Fetch regardless of state so a partially-purged document can be retried.
    doc = await session.scalar(
        select(UserDocument).where(
            UserDocument.id == document_id, UserDocument.owner_id == user.id
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    # 1. Revoke access immediately and durably (§9.4 step 1).
    if doc.state != "deleted":
        doc.state = "deleted"
        await session.commit()

    # 2. Idempotently purge derived data + blobs, then the row (retry-until-complete).
    await purge_document(session, store, doc)

    # 3. Content-free completion record (§9.4 step 4).
    record_audit(
        session,
        actor_user_id=user.id,
        action="document.delete",
        resource_class="user_document",
        resource_id=document_id,
    )
    await session.commit()


@router.get("/documents/{document_id}/bookmarks", response_model=list[BookmarkRead])
async def list_bookmarks(
    document_id: str, user: CurrentUser, session: DbSession
) -> list[BookmarkRead]:
    await _owned_document(session, user, document_id)
    rows = await session.scalars(
        select(Bookmark)
        .where(Bookmark.document_id == document_id, Bookmark.owner_id == user.id)
        .order_by(Bookmark.created_at.desc())
    )
    return [BookmarkRead.model_validate(b) for b in rows]


@router.post(
    "/documents/{document_id}/bookmarks",
    response_model=BookmarkRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_bookmark(
    document_id: str, body: BookmarkCreate, user: CurrentUser, session: DbSession
) -> BookmarkRead:
    await _owned_document(session, user, document_id)
    bookmark = Bookmark(
        owner_id=user.id,
        document_id=document_id,
        entry_id=body.entry_id,
        label=body.label,
        color=body.color,
    )
    session.add(bookmark)
    await session.commit()
    await session.refresh(bookmark)
    return BookmarkRead.model_validate(bookmark)


@router.delete("/bookmarks/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bookmark(bookmark_id: str, user: CurrentUser, session: DbSession) -> None:
    bookmark = await session.scalar(
        select(Bookmark).where(Bookmark.id == bookmark_id, Bookmark.owner_id == user.id)
    )
    if bookmark is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bookmark not found")
    await session.delete(bookmark)
    await session.commit()


@router.get("/documents/{document_id}/annotations", response_model=list[AnnotationRead])
async def list_annotations(
    document_id: str, user: CurrentUser, session: DbSession
) -> list[AnnotationRead]:
    await _owned_document(session, user, document_id)
    rows = await session.scalars(
        select(Annotation)
        .where(Annotation.document_id == document_id, Annotation.owner_id == user.id)
        .order_by(Annotation.created_at.desc())
    )
    return [AnnotationRead.model_validate(a) for a in rows]


@router.post(
    "/documents/{document_id}/annotations",
    response_model=AnnotationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_annotation(
    document_id: str, body: AnnotationCreate, user: CurrentUser, session: DbSession
) -> AnnotationRead:
    await _owned_document(session, user, document_id)
    annotation = Annotation(
        owner_id=user.id,
        document_id=document_id,
        target_entry_id=body.target_entry_id,
        note=body.note,
    )
    session.add(annotation)
    await session.commit()
    await session.refresh(annotation)
    return AnnotationRead.model_validate(annotation)


@router.delete("/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_annotation(annotation_id: str, user: CurrentUser, session: DbSession) -> None:
    annotation = await session.scalar(
        select(Annotation).where(Annotation.id == annotation_id, Annotation.owner_id == user.id)
    )
    if annotation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    await session.delete(annotation)
    await session.commit()


@router.patch("/annotations/{annotation_id}", response_model=AnnotationRead)
async def update_annotation(
    annotation_id: str, body: AnnotationUpdate, user: CurrentUser, session: DbSession
) -> AnnotationRead:
    annotation = await session.scalar(
        select(Annotation).where(Annotation.id == annotation_id, Annotation.owner_id == user.id)
    )
    if annotation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Annotation not found")
    annotation.note = body.note
    await session.commit()
    await session.refresh(annotation)
    return AnnotationRead.model_validate(annotation)


@router.get(
    "/documents/{document_id}/pdf-annotations", response_model=list[PdfAnnotationRead]
)
async def list_pdf_annotations(
    document_id: str, user: CurrentUser, session: DbSession
) -> list[PdfAnnotationRead]:
    await _owned_document(session, user, document_id)
    rows = await session.scalars(
        select(PdfAnnotation)
        .where(PdfAnnotation.document_id == document_id, PdfAnnotation.owner_id == user.id)
        .order_by(PdfAnnotation.created_at.asc())
    )
    return [PdfAnnotationRead.model_validate(annotation) for annotation in rows]


@router.post(
    "/documents/{document_id}/pdf-annotations",
    response_model=PdfAnnotationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_pdf_annotation(
    document_id: str, body: PdfAnnotationCreate, user: CurrentUser, session: DbSession
) -> PdfAnnotationRead:
    await _owned_document(session, user, document_id)
    annotation = PdfAnnotation(
        owner_id=user.id,
        document_id=document_id,
        kind=body.kind,
        page_index=body.page_index,
        geometry=body.geometry,
        color=body.color,
        note=body.note.strip() if body.note else None,
    )
    session.add(annotation)
    await session.commit()
    await session.refresh(annotation)
    return PdfAnnotationRead.model_validate(annotation)


@router.delete("/pdf-annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pdf_annotation(
    annotation_id: str, user: CurrentUser, session: DbSession
) -> None:
    annotation = await session.scalar(
        select(PdfAnnotation).where(
            PdfAnnotation.id == annotation_id, PdfAnnotation.owner_id == user.id
        )
    )
    if annotation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF annotation not found")
    await session.delete(annotation)
    await session.commit()


@router.get("/documents/{document_id}/overrides", response_model=list[OverrideRead])
async def list_overrides(
    document_id: str, user: CurrentUser, session: DbSession
) -> list[OverrideRead]:
    await _owned_document(session, user, document_id)
    rows = await session.scalars(
        select(ReferenceLinkOverride).where(
            ReferenceLinkOverride.document_id == document_id,
            ReferenceLinkOverride.owner_id == user.id,
        )
    )
    return [OverrideRead.model_validate(o) for o in rows]


@router.put("/documents/{document_id}/overrides", response_model=OverrideRead)
async def upsert_override(
    document_id: str, body: OverrideUpsert, user: CurrentUser, session: DbSession
) -> OverrideRead:
    """Record (or replace) the user's choice for one mention's callout (§12.7)."""
    await _owned_document(session, user, document_id)
    existing = await session.scalar(
        select(ReferenceLinkOverride).where(
            ReferenceLinkOverride.document_id == document_id,
            ReferenceLinkOverride.owner_id == user.id,
            ReferenceLinkOverride.entry_id == body.entry_id,
            ReferenceLinkOverride.span_start == body.span_start,
            ReferenceLinkOverride.span_end == body.span_end,
        )
    )
    if existing is not None:
        existing.callout_id = body.callout_id
        override = existing
    else:
        override = ReferenceLinkOverride(
            owner_id=user.id,
            document_id=document_id,
            entry_id=body.entry_id,
            span_start=body.span_start,
            span_end=body.span_end,
            callout_id=body.callout_id,
        )
        session.add(override)
    await session.commit()
    await session.refresh(override)
    return OverrideRead.model_validate(override)


@router.delete("/overrides/{override_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_override(override_id: str, user: CurrentUser, session: DbSession) -> None:
    override = await session.scalar(
        select(ReferenceLinkOverride).where(
            ReferenceLinkOverride.id == override_id, ReferenceLinkOverride.owner_id == user.id
        )
    )
    if override is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Override not found")
    await session.delete(override)
    await session.commit()
