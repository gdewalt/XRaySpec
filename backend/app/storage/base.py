"""Object-store abstraction (DESIGN.md §5, §9, §11.1).

The browser uploads directly to private storage via a short-lived, single-purpose
grant with a server-selected key (§5.2). The API never trusts the client's claims
about the object: at finalize it reads the object back to observe size, hash, and
magic bytes.

An interface with a Supabase implementation (prod) and an in-memory
implementation (tests) keeps the finalize logic testable without a live bucket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class UploadGrant:
    """A single-purpose, expiring direct-upload grant."""

    object_key: str
    url: str
    method: str
    headers: dict[str, str]
    max_bytes: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ObjectStat:
    size: int
    content_type: str | None


class ObjectStore(Protocol):
    async def presign_upload(
        self,
        object_key: str,
        *,
        max_bytes: int,
        content_type: str | None,
        expires_in: int,
    ) -> UploadGrant: ...

    async def read(self, object_key: str, *, limit: int) -> bytes:
        """Return the object's bytes, raising if it exceeds ``limit`` or is absent."""
        ...

    async def stat(self, object_key: str) -> ObjectStat | None: ...

    async def delete(self, object_key: str) -> None: ...
