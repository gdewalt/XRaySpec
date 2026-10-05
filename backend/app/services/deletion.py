"""Document deletion workflow (DESIGN.md §9.4).

After the API revokes access synchronously, this removes the document's derived
data and blobs: bookmarks, annotations, jobs, immutable artifacts (and their
object-storage blobs), and the source PDF + row when no other document still
references it. It is **idempotent** — safe to re-run to completion after a
partial failure (retry-until-complete).

Object-version and backup expiry are handled by storage lifecycle policy and
disclosed separately (§9.4); this removes the live objects.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Annotation,
    Bookmark,
    ExtractionArtifact,
    ExtractionJob,
    JobCheckpoint,
    PdfAnnotation,
    ReferenceLinkOverride,
    SourceDocument,
    UserDocument,
)
from ..storage.base import ObjectStore


async def _delete_blob(store: ObjectStore, object_key: str | None) -> None:
    if object_key:
        await store.delete(object_key)  # idempotent per the ObjectStore contract


async def purge_document(session: AsyncSession, store: ObjectStore, doc: UserDocument) -> None:
    """Idempotently delete a document's derived data, blobs, and the row itself.

    The caller must have already revoked access (set ``state='deleted'``).
    """
    await session.execute(delete(Bookmark).where(Bookmark.document_id == doc.id))
    await session.execute(delete(Annotation).where(Annotation.document_id == doc.id))
    await session.execute(delete(PdfAnnotation).where(PdfAnnotation.document_id == doc.id))
    await session.execute(
        delete(ReferenceLinkOverride).where(ReferenceLinkOverride.document_id == doc.id)
    )
    await session.execute(delete(ExtractionJob).where(ExtractionJob.document_id == doc.id))

    source = await session.get(SourceDocument, doc.source_id)
    if source is not None:
        artifacts = await session.scalars(
            select(ExtractionArtifact).where(ExtractionArtifact.source_id == source.id)
        )
        for artifact in artifacts:
            await _delete_blob(store, artifact.manifest_object_key)
            await session.delete(artifact)

        # Only delete the source (and its PDF blob + resume checkpoints) if no other
        # document shares it.
        shared = await session.scalar(
            select(func.count())
            .select_from(UserDocument)
            .where(UserDocument.source_id == source.id, UserDocument.id != doc.id)
        )
        if not shared:
            await session.execute(
                delete(JobCheckpoint).where(JobCheckpoint.source_id == source.id)
            )
            await _delete_blob(store, source.pdf_object_key)
            await session.delete(source)

    await session.delete(doc)
    await session.commit()
