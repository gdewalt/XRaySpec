"""Atomic artifact publication (DESIGN.md §9.2).

Writes the immutable entries+manifest blob to object storage, then in one
transaction records the ready artifact, marks it active (demoting the prior
active to rollback), and points the document at it. Only ready artifacts are
returned by the API.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ExtractionArtifact, UserDocument, new_id
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

    # Demote the current active artifact for this source to rollback.
    prior = await session.scalars(
        select(ExtractionArtifact).where(
            ExtractionArtifact.source_id == source_id, ExtractionArtifact.is_active.is_(True)
        )
    )
    for old in prior:
        old.is_active = False
        old.is_rollback = True

    record = ExtractionArtifact(
        id=artifact_id,
        source_id=source_id,
        schema_version=artifact.schema_version,
        disposition=artifact.disposition,
        manifest_object_key=key,
        is_active=True,
        ready_at=datetime.now(UTC),
    )
    session.add(record)

    doc = await session.get(UserDocument, document_id)
    if doc is not None and doc.state != "deleted":
        doc.active_artifact_id = artifact_id
        doc.state = _DISPOSITION_TO_STATE.get(artifact.disposition, "ready")

    await session.commit()
    return artifact_id
