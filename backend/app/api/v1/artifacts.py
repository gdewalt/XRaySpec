"""Artifact reading API (DESIGN.md §14.1).

Serves the immutable artifact to the viewer: lightweight metadata, and the full
entries + mention indices (read back from the object-storage blob). Owner-scoped
via the artifact's source — a foreign artifact is a 404 (§14.2).
"""

from __future__ import annotations

import gzip
import json

from fastapi import APIRouter, HTTPException, status

from ...config import get_settings
from ...db.models import ExtractionArtifact, SourceDocument
from ...enrichment.align import repair_serialized_display_overlaps
from ...schemas.artifacts import ArtifactEntriesResponse, ArtifactRead
from ..deps import CurrentUser, DbSession, Storage

router = APIRouter(tags=["artifacts"])


async def _owned_artifact(session, user, artifact_id: str) -> ExtractionArtifact:
    artifact = await session.get(ExtractionArtifact, artifact_id)
    if artifact is not None:
        source = await session.get(SourceDocument, artifact.source_id)
        if source is not None and source.owner_id == user.id:
            return artifact
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")


@router.get("/artifacts/{artifact_id}", response_model=ArtifactRead)
async def get_artifact(artifact_id: str, user: CurrentUser, session: DbSession) -> ArtifactRead:
    return ArtifactRead.model_validate(await _owned_artifact(session, user, artifact_id))


@router.get("/artifacts/{artifact_id}/entries", response_model=ArtifactEntriesResponse)
async def get_artifact_entries(
    artifact_id: str, user: CurrentUser, session: DbSession, store: Storage
) -> ArtifactEntriesResponse:
    artifact = await _owned_artifact(session, user, artifact_id)
    if not artifact.manifest_object_key:
        raise HTTPException(status.HTTP_409_CONFLICT, "Artifact has no materialized content")

    raw = await store.read(artifact.manifest_object_key, limit=get_settings().max_upload_bytes)
    data = json.loads(gzip.decompress(raw))
    return ArtifactEntriesResponse(
        artifact_id=artifact.id,
        doc_type=data.get("doc_type"),
        mode=data.get("mode"),
        disposition=data.get("disposition"),
        page_count=data.get("page_count"),
        warnings=data.get("warnings", []),
        front_matter=data.get("front_matter"),
        entries=repair_serialized_display_overlaps(data.get("entries", [])),
        figure_mentions=data.get("figure_mentions", []),
        numeral_mentions=data.get("numeral_mentions", []),
        figure_occurrences=data.get("figure_occurrences", []),
        mention_associations=data.get("mention_associations", []),
        callout_occurrences=data.get("callout_occurrences", []),
    )
