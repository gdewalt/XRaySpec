"""Direct-upload ingestion (DESIGN.md §11.1, §14.1).

Two steps: (1) ``POST /uploads`` mints a single-purpose, expiring grant with a
server-selected key and byte ceiling; the browser uploads the PDF straight to
private storage. (2) ``POST /uploads/{id}/complete`` finalizes — the server reads
the object back to *observe* size, SHA-256, and magic bytes, then creates the
source/user document and queues an extraction job.

The web tier NEVER parses the PDF (DESIGN.md §17.2): it only checks the ``%PDF-``
magic marker and size/hash. Page count, encryption, and real validation happen
in the isolated worker.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ...config import get_settings
from ...db.models import ExtractionJob, SourceDocument, Upload, UserDocument, new_id
from ...schemas.documents import DocumentRead
from ...schemas.uploads import (
    UploadComplete,
    UploadCompleteResponse,
    UploadCreate,
    UploadGrantResponse,
)
from ...services.audit import record_audit
from ..deps import CurrentUser, DbSession, Storage

router = APIRouter(tags=["uploads"])

PDF_MAGIC = b"%PDF-"


@router.post("/uploads", response_model=UploadGrantResponse, status_code=status.HTTP_201_CREATED)
async def create_upload(
    body: UploadCreate, user: CurrentUser, session: DbSession, store: Storage
) -> UploadGrantResponse:
    settings = get_settings()
    object_key = f"sources/{user.id}/{new_id('obj')}.pdf"
    ttl = settings.upload_grant_ttl_seconds
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl)

    upload = Upload(
        owner_id=user.id,
        object_key=object_key,
        max_bytes=settings.max_upload_bytes,
        content_type=body.content_type,
        original_filename=body.filename,
        status="pending",
        expires_at=expires_at,
    )
    session.add(upload)

    grant = await store.presign_upload(
        object_key,
        max_bytes=settings.max_upload_bytes,
        content_type=body.content_type,
        expires_in=ttl,
    )
    record_audit(
        session,
        actor_user_id=user.id,
        action="upload.create",
        resource_class="upload",
        resource_id=upload.id,
    )
    await session.commit()
    return UploadGrantResponse(
        upload_id=upload.id,
        object_key=grant.object_key,
        url=grant.url,
        method=grant.method,
        headers=grant.headers,
        max_bytes=grant.max_bytes,
        expires_at=grant.expires_at,
    )


@router.post(
    "/uploads/{upload_id}/complete",
    response_model=UploadCompleteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_upload(
    upload_id: str, body: UploadComplete, user: CurrentUser, session: DbSession, store: Storage
) -> UploadCompleteResponse:
    upload = await session.scalar(
        select(Upload).where(Upload.id == upload_id, Upload.owner_id == user.id)
    )
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    if upload.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload grant already used")

    # Observe the uploaded object (never parse it here — §17.2).
    try:
        data = await store.read(upload.object_key, limit=upload.max_bytes)
    except FileNotFoundError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No object was uploaded for this grant"
        ) from None
    except ValueError:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "SOURCE_LIMIT_EXCEEDED"
        ) from None

    if PDF_MAGIC not in data[:1024]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "INVALID_PDF")

    sha256 = hashlib.sha256(data).hexdigest()
    byte_size = len(data)

    source = SourceDocument(
        owner_id=user.id,
        source_type="upload",
        pdf_object_key=upload.object_key,
        sha256=sha256,
        byte_size=byte_size,
        original_filename=upload.original_filename,
        state="uploaded",
    )
    session.add(source)
    await session.flush()

    title = body.title or upload.original_filename or "Untitled document"
    doc = UserDocument(owner_id=user.id, source_id=source.id, title=title, state="processing")
    session.add(doc)
    await session.flush()

    job = ExtractionJob(owner_id=user.id, document_id=doc.id, status="queued")
    session.add(job)
    await session.flush()

    upload.status = "completed"
    upload.sha256 = sha256
    upload.byte_size = byte_size

    record_audit(
        session, actor_user_id=user.id, action="upload.complete",
        resource_class="upload", resource_id=upload.id,
    )
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
    return UploadCompleteResponse(
        document=DocumentRead.model_validate(doc), job_id=job.id, job_status=job.status
    )
