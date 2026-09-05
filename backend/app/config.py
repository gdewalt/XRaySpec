"""Application settings (DESIGN.md §20, §25.2).

Instance-level operator configuration, loaded from the environment. There is no
``Tenant`` entity; retention/enrichment defaults are instance settings.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="XRAY_", extra="ignore")

    environment: str = "development"

    # Postgres (Supabase managed). The FastAPI service is the only DB client.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/xray"

    # Supabase Auth — JWT verification (DESIGN.md §17.1). Never a client-direct/RLS path.
    supabase_jwt_secret: str = Field(default="", description="Supabase JWT signing secret")
    supabase_project_url: str = ""

    # Object storage (Supabase Storage or external S3-compatible; §23 #1 open).
    storage_backend: str = "supabase"  # "supabase" | "local" (dev) | "memory" (tests)
    storage_bucket: str = "xray-sources"
    local_storage_dir: str = "./devstorage"
    supabase_service_key: str = Field(default="", description="Supabase service-role key")

    # Ingestion safety bounds (§11.1, §11.3, §17.6)
    max_upload_bytes: int = 100 * 1024 * 1024  # 100 MiB
    upload_grant_ttl_seconds: int = 3600
    max_import_bytes: int = 10 * 1024 * 1024  # 10 MiB portable-save cap

    # Restricted outbound fetch (§11.2, §17.4)
    fetch_allowed_hosts: list[str] = Field(
        default_factory=lambda: [
            "patents.google.com",
            "patentimages.storage.googleapis.com",
            "storage.googleapis.com",
        ]
    )
    fetch_max_html_bytes: int = 5 * 1024 * 1024
    fetch_max_pdf_bytes: int = 100 * 1024 * 1024
    fetch_max_redirects: int = 5

    # Access control: allowlist of permitted user emails (§3.1, single-tenant).
    allowed_emails: list[str] = Field(default_factory=list)

    # Global concurrency constant (§17.6) — not a scheduler.
    max_concurrent_extractions: int = 2

    # HTTP hardening (§17, §19.6). Rate limit is best-effort per replica; 0 disables.
    rate_limit_per_minute: int = 0
    trust_forwarded_for: bool = False

    # Instance policy defaults (§9.3, §13.1)
    enrichment_enabled: bool = True
    default_retention_days: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()
