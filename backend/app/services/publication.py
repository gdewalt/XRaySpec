"""Atomic artifact publication (DESIGN.md §9.2).

Writes the immutable entries+manifest blob to object storage, then in one
transaction records the ready artifact, marks it active (demoting the prior
active to rollback), and points the document at it. Only ready artifacts are
returned by the API.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ExtractionArtifact, SourceDocument, UserDocument, new_id
from ..extraction.artifact import Artifact
from ..storage.base import ObjectStore

_DISPOSITION_TO_STATE = {
    "complete": "ready",
    "complete_with_warnings": "ready_with_warnings",
    "partial": "ready_with_warnings",
    "failed": "failed",
}


def _artifact_payload(artifact: Artifact) -> dict:
    # asdict recurses the frozen dataclasses (entries, locators, provenance).
    return asdict(artifact)


@dataclass(frozen=True, slots=True)
class ReusableArtifact:
    """A verified final artifact blob matching the current source and config."""

    artifact_id: str
    source_id: str
    blob: bytes
    schema_version: int
    disposition: str
    document_state: str


async def find_reusable_artifact(
    session: AsyncSession,
    store: ObjectStore,
    *,
    owner_id: str,
    source_sha256: str,
    config_hash: str,
    exclude_source_id: str | None = None,
    max_bytes: int,
) -> ReusableArtifact | None:
    """Find a completed owner-scoped artifact with the exact PDF/config identity."""
    query = (
        select(ExtractionArtifact)
        .join(SourceDocument, SourceDocument.id == ExtractionArtifact.source_id)
        .where(
            SourceDocument.owner_id == owner_id,
            SourceDocument.sha256 == source_sha256,
            ExtractionArtifact.manifest_object_key.is_not(None),
            ExtractionArtifact.ready_at.is_not(None),
            ExtractionArtifact.is_active.is_(True),
        )
        .order_by(ExtractionArtifact.ready_at.desc())
    )
    if exclude_source_id is not None:
        query = query.where(ExtractionArtifact.source_id != exclude_source_id)

    for record in await session.scalars(query):
        try:
            blob = await store.read(record.manifest_object_key, limit=max_bytes)
            payload = json.loads(gzip.decompress(blob))
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("source_sha256") == source_sha256
            and payload.get("config_hash") == config_hash
            and payload.get("schema_version") == record.schema_version
            and payload.get("disposition") in _DISPOSITION_TO_STATE
        ):
            return ReusableArtifact(
                artifact_id=record.id,
                source_id=record.source_id,
                blob=blob,
                schema_version=record.schema_version,
                disposition=str(payload["disposition"]),
                document_state=_DISPOSITION_TO_STATE[str(payload["disposition"])],
            )
    return None


async def _activate_artifact(
    session: AsyncSession,
    *,
    artifact_id: str,
    key: str,
    document_id: str,
    source_id: str,
    schema_version: int,
    disposition: str,
) -> None:
    # Demote the current active artifact for this source to rollback.
    prior = await session.scalars(
        select(ExtractionArtifact).where(
            ExtractionArtifact.source_id == source_id, ExtractionArtifact.is_active.is_(True)
        )
    )
    for old in prior:
        old.is_active = False
        old.is_rollback = True

    session.add(
        ExtractionArtifact(
            id=artifact_id,
            source_id=source_id,
            schema_version=schema_version,
            disposition=disposition,
            manifest_object_key=key,
            is_active=True,
            ready_at=datetime.now(UTC),
        )
    )

    doc = await session.get(UserDocument, document_id)
    if doc is not None and doc.state != "deleted":
        doc.active_artifact_id = artifact_id
        doc.state = _DISPOSITION_TO_STATE.get(disposition, "ready")


async def publish_reused_artifact(
    session: AsyncSession,
    store: ObjectStore,
    *,
    document_id: str,
    source_id: str,
    owner_id: str,
    reusable: ReusableArtifact,
) -> str:
    """Clone a verified final blob so each source retains independent lifecycle."""
    artifact_id = new_id("art")
    key = f"artifacts/{owner_id}/{artifact_id}.json.gz"
    await store.write(key, reusable.blob, content_type="application/gzip")
    await _activate_artifact(
        session,
        artifact_id=artifact_id,
        key=key,
        document_id=document_id,
        source_id=source_id,
        schema_version=reusable.schema_version,
        disposition=reusable.disposition,
    )
    await session.commit()
    return artifact_id


async def publish_artifact(
    session: AsyncSession,
    store: ObjectStore,
    *,
    document_id: str,
    source_id: str,
    owner_id: str,
    artifact: Artifact,
) -> str:
    artifact_id = new_id("art")
    key = f"artifacts/{owner_id}/{artifact_id}.json.gz"
    blob = gzip.compress(json.dumps(_artifact_payload(artifact)).encode("utf-8"))
    await store.write(key, blob, content_type="application/gzip")

    await _activate_artifact(
        session,
        artifact_id=artifact_id,
        key=key,
        document_id=document_id,
        source_id=source_id,
        schema_version=artifact.schema_version,
        disposition=artifact.disposition,
    )

    await session.commit()
    return artifact_id
