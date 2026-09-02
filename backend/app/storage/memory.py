"""In-memory object store for tests (DESIGN.md §11.1).

Not for production. ``put`` simulates the browser's direct upload so the
finalize flow can be exercised end-to-end without a live bucket.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .base import ObjectStat, UploadGrant


class MemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    async def presign_upload(
        self,
        object_key: str,
        *,
        max_bytes: int,
        content_type: str | None,
        expires_in: int,
    ) -> UploadGrant:
        return UploadGrant(
            object_key=object_key,
            url=f"memory://upload/{object_key}",
            method="PUT",
            headers={},
            max_bytes=max_bytes,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )

    async def write(
        self, object_key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        self._objects[object_key] = data

    async def read(self, object_key: str, *, limit: int) -> bytes:
        data = self._objects.get(object_key)
        if data is None:
            raise FileNotFoundError(object_key)
        if len(data) > limit:
            raise ValueError("object exceeds limit")
        return data

    async def stat(self, object_key: str) -> ObjectStat | None:
        data = self._objects.get(object_key)
        if data is None:
            return None
        return ObjectStat(size=len(data), content_type="application/pdf")

    async def delete(self, object_key: str) -> None:
        self._objects.pop(object_key, None)

    # --- test helper (not part of the ObjectStore protocol) ---
    async def put(self, object_key: str, data: bytes) -> None:
        self._objects[object_key] = data
