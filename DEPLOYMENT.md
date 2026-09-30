# Production deployment: Render + Supabase

The root `render.yaml` provisions one same-origin web service and one background
OCR worker from the root `Dockerfile`. PostgreSQL, Auth, and private object
storage are provided by Supabase.

## 1. Supabase

Create a production project in the same geographic area as Render (`oregon` in
the Blueprint). Enable email magic links. Add each approved user through Auth
before they sign in; X-Ray Spec does not permit public self-registration.

The `xray-sources` bucket must stay private and permit all content types written
by the application:

- `application/pdf`
- `application/json`
- `application/gzip`
- `application/octet-stream`

This deployment sets source and fetched-PDF limits to 50 MiB to match the
current bucket limit.

Set the following locally and run the idempotent bucket provisioner from
`backend/`:

```powershell
$env:XRAY_SUPABASE_PROJECT_URL = "https://PROJECT.supabase.co"
$env:XRAY_SUPABASE_SERVICE_KEY = "YOUR_SECRET_OR_SERVICE_ROLE_KEY"
.venv\Scripts\python.exe -m scripts.provision_supabase
```

Copy the **Session pooler** PostgreSQL connection string (port `5432`) and
change its scheme to `postgresql+asyncpg://`. Render is a persistent service,
and session mode is the IPv4-compatible option that supports normal SQLAlchemy
and asyncpg session behavior. Percent-encode reserved characters in the
database password.

## 2. Render Blueprint

Push this repository to a private Git provider repository connected to Render,
then create a Blueprint from `render.yaml`. Render prompts for the secret values
on both services:

- `XRAY_DATABASE_URL`
- `XRAY_SUPABASE_PROJECT_URL`
- `XRAY_SUPABASE_ANON_KEY` (or publishable key)
- `XRAY_SUPABASE_SERVICE_KEY` (or secret key)
- `XRAY_ALLOWED_EMAILS` as JSON, for example
  `["owner@example.com","reviewer@example.com"]`

The web pre-deploy command applies Alembic migrations exactly once per deploy.
The worker uses 1 CPU / 2 GB because 300-DPI OCR is memory intensive. Automatic
deploys start disabled so a migration and worker image can be canaried together.

Current Supabase projects use asymmetric Auth signing keys; the API reads their
public JWKS from `XRAY_SUPABASE_PROJECT_URL`. If this specific project still
issues legacy HS256 tokens, manually add `XRAY_SUPABASE_JWT_SECRET` to both
services after the Blueprint is created.

## 3. Auth redirect and launch checks

After Render assigns the web URL, add it as the Supabase Auth Site URL and an
allowed redirect URL. Then verify:

1. `GET /live` and `GET /ready` return 200.
2. An invited, allowlisted email receives a magic link and signs in.
3. A non-allowlisted account receives 403 from the API.
4. Upload one PDF and fetch one patent number; both reach `ready` through SSE.
5. Open the source PDF, add/edit a note, and delete the test document.

The current worker combines restricted fetching, OCR extraction, and clean-text
enrichment because the queue does not yet encode role-specific stages. Render
therefore does not yet enforce the design's intended network separation between
fetch and extraction roles. Do not describe that isolation as a deployed
control until the queue is split by role.
