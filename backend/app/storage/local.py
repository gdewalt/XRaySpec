"""Local-filesystem object store for development (DESIGN.md §11.1).

Not for production. Persists blobs on disk so multiple processes (a seed script,
the API, the worker) share them without Supabase. Direct browser upload is not
supported for this backend (there is no signed PUT endpoint); use the API's
import/fetch flows instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from .base import ObjectStat, UploadGrant


class LocalFileObjectStore:
    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _path(self, object_key: str) -> Path:
        return self._root / object_key

    async def presign_upload(
        self, object_key: str, *, max_bytes: int, content_type: str | None, expires_in: int
    ) -> UploadGrant:
        return UploadGrant(
            object_key=object_key,
            url=f"local://{object_key}",  # not a real PUT endpoint
            method="PUT",
            headers={},
            max_bytes=max_bytes,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )

    async def write(
        self, object_key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def read(self, object_key: str, *, limit: int) -> bytes:
        path = self._path(object_key)
        if not path.exists():
            raise FileNotFoundError(object_key)
        data = path.read_bytes()
        if len(data) > limit:
            raise ValueError("object exceeds limit")
        return data

    async def stat(self, object_key: str) -> ObjectStat | None:
        path = self._path(object_key)
        if not path.exists():
            return None
        return ObjectStat(size=path.stat().st_size, content_type=None)

    async def delete(self, object_key: str) -> None:
        self._path(object_key).unlink(missing_ok=True)
