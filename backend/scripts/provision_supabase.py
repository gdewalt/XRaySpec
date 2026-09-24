"""Create and verify X-Ray Spec's private Supabase Storage bucket.

Database schema provisioning remains Alembic's job and runs through Render's
pre-deploy command. This script is intentionally idempotent.
"""

from __future__ import annotations

import asyncio

import httpx

from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    if not settings.supabase_project_url or not settings.supabase_service_key:
        raise SystemExit("Set XRAY_SUPABASE_PROJECT_URL and XRAY_SUPABASE_SERVICE_KEY first.")

    base = f"{settings.supabase_project_url.rstrip('/')}/storage/v1"
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "apikey": settings.supabase_service_key,
        "Content-Type": "application/json",
    }
    bucket = settings.storage_bucket
    async with httpx.AsyncClient(timeout=30) as client:
        existing = await client.get(f"{base}/bucket/{bucket}", headers=headers)
        if existing.status_code == 404:
            created = await client.post(
                f"{base}/bucket",
                headers=headers,
                json={
                    "id": bucket,
                    "name": bucket,
                    "public": False,
                    "file_size_limit": settings.max_upload_bytes,
                    "allowed_mime_types": [
                        "application/pdf",
                        "application/gzip",
                        "application/json",
                        "application/octet-stream",
                    ],
                },
            )
            created.raise_for_status()
            print(f"Created private bucket: {bucket}")
        else:
            existing.raise_for_status()
            info = existing.json()
            if info.get("public"):
                raise SystemExit(f"Bucket {bucket!r} exists but is public; make it private.")
            print(f"Private bucket already exists: {bucket}")


if __name__ == "__main__":
    asyncio.run(main())
