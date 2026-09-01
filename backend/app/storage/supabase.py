"""Supabase Storage implementation (DESIGN.md §25.2).

Uses the service-role key server-side; the bucket is private (§17.5). The browser
uploads via a signed upload URL and never sees the service key.

NOTE: the exact Supabase Storage REST endpoints below should be verified against
a live project before relying on them in production; the shapes follow Supabase's
documented Storage API. The finalize *logic* is provider-agnostic and covered by
tests via the in-memory store.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from .base import ObjectStat, UploadGrant


class SupabaseObjectStore:
    def __init__(self, project_url: str, service_key: str, bucket: str) -> None:
        self._base = f"{project_url.rstrip('/')}/storage/v1"
        self._bucket = bucket
        self._auth = {"Authorization": f"Bearer {service_key}"}

    async def presign_upload(
        self,
        object_key: str,
        *,
        max_bytes: int,
        content_type: str | None,
        expires_in: int,
    ) -> UploadGrant:
        # POST /object/upload/sign/{bucket}/{path} -> {"url": "/object/upload/sign/...token=..."}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self._base}/object/upload/sign/{self._bucket}/{object_key}",
                headers=self._auth,
            )
            resp.raise_for_status()
            signed = resp.json()["url"]
        return UploadGrant(
            object_key=object_key,
            url=f"{self._base}{signed}",
            method="PUT",
            headers={"Content-Type": content_type or "application/pdf"},
            max_bytes=max_bytes,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )

    async def read(self, object_key: str, *, limit: int) -> bytes:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "GET", f"{self._base}/object/{self._bucket}/{object_key}", headers=self._auth
            ) as resp:
                if resp.status_code == 404:
                    raise FileNotFoundError(object_key)
                resp.raise_for_status()
                chunks = bytearray()
                async for chunk in resp.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > limit:
                        raise ValueError("object exceeds limit")
                return bytes(chunks)

    async def stat(self, object_key: str) -> ObjectStat | None:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self._base}/object/info/{self._bucket}/{object_key}", headers=self._auth
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            info = resp.json()
            return ObjectStat(
                size=int(info.get("size", 0)),
                content_type=info.get("contentType") or info.get("mimetype"),
            )

    async def delete(self, object_key: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"{self._base}/object/{self._bucket}/{object_key}", headers=self._auth
            )
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()
