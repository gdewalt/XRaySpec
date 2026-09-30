# Replit + Supabase production deployment

This repository is configured as one Replit **Reserved VM** deployment:

- FastAPI listens on the single public port and serves both `/api/v1/*` and the
  built React SPA.
- One extraction worker runs beside the API and claims durable jobs from
  Supabase Postgres.
- Supabase provides Auth, Postgres, and a private Storage bucket.

Reserved VM is required for the continuously running worker. Do not use an
Autoscale deployment for this topology: it can scale to zero and terminate
background extraction work.

## 1. Create the Supabase project

1. Create a project in the Supabase region nearest the intended Replit
   deployment region.
2. In **Storage**, create a private bucket named `xray-sources`.
3. Set the bucket file-size limit to `100 MB` and allowed MIME type to
   `application/pdf` where the dashboard exposes those controls.
4. In **Authentication**, enable the desired provider and create/invite every
   email that will appear in `XRAY_ALLOWED_EMAILS`.
5. Add the final Replit URL and any custom domain to Auth's allowed redirect
   URLs.

The backend is the only database client. Do not expose the database password or
Supabase secret key to frontend code, and do not add browser RLS policies for
the application tables.

## 2. Collect Supabase connection values

From **Connect**, copy the **Session pooler** connection string. It uses port
`5432`, works from IPv4 networks, and is appropriate for this persistent VM.
Change the scheme to `postgresql+asyncpg://` and percent-encode reserved
characters in the database password.

From **Project Settings → API Keys**, copy the browser-safe publishable key and a
server-side `sb_secret_...` key. The publishable key is intentionally returned
to the browser by `/runtime-config.js`; the secret key bypasses RLS and must
exist only in Replit Deployment Secrets. The API verifies current asymmetric
access tokens against the project's public JWKS endpoint automatically. Only a
legacy HS256 project needs `XRAY_SUPABASE_JWT_SECRET`.

## 3. Import into Replit

Import the Git repository into a new Replit App. Replit reads `.replit` and
`replit.nix`; the latter installs the Tesseract and Poppler system binaries used
by extraction.

In **Secrets**, add every value shown in
`backend/.env.production.example`. At minimum:

| Secret | Value |
|---|---|
| `XRAY_ENVIRONMENT` | `production` |
| `XRAY_DATABASE_URL` | asyncpg Session-pooler URL |
| `XRAY_SUPABASE_PROJECT_URL` | `https://PROJECT_REF.supabase.co` |
| `XRAY_SUPABASE_ANON_KEY` | browser-safe publishable key |
| `XRAY_SUPABASE_SERVICE_KEY` | server-side `sb_secret_...` key |
| `XRAY_STORAGE_BUCKET` | `xray-sources` |
| `XRAY_ALLOWED_EMAILS` | JSON array, e.g. `["owner@example.com"]` |

Do not create a `.env` file in Replit and do not prefix server secrets with
`VITE_`; Vite-prefixed values are embedded into browser assets.

## 4. Publish

Open **Publishing** and select **Reserved VM** with the **Web server** app type.
Use at least 4 vCPU / 8 GB RAM for OCR workloads, then tune after measuring the
corpus. The checked-in configuration supplies:

```text
Build command: bash scripts/replit-build.sh
Run command:   bash scripts/replit-run.sh
Internal port: 3000
External port: 80
```

The build installs Python dependencies, performs a reproducible `npm ci`, and
builds the SPA. Startup validates secrets, initializes/migrates the database,
then supervises both the API and worker.

## 5. Verify the deployment

After publishing, verify:

```text
GET https://YOUR_APP.replit.app/live
GET https://YOUR_APP.replit.app/ready
GET https://YOUR_APP.replit.app/
```

Then sign in, upload a small PDF, and confirm that its job advances. Inspect the
Replit deployment logs for both Uvicorn and worker failures.

## Operational notes

- Replit exposes one external port; the React build is therefore served by
  FastAPI rather than a separate Vite server.
- Start with one VM and `XRAY_MAX_CONCURRENT_EXTRACTIONS=1`. OCR is both CPU-
  and memory-intensive.
- Supabase Storage remains private. Browser uploads use short-lived,
  server-selected signed grants.
- Rotate Supabase secret keys regularly and immediately after suspected
  exposure.
- Database startup runs the committed Alembic revisions before accepting
  traffic. Review every future migration before republishing.
- The current worker combines restricted-egress fetching and extraction in one
  process. On a single Replit VM that means the extraction process also has
  outbound network access, which does not yet satisfy the design's strict
  worker-isolation boundary. Split these into separate deployment roles before
  treating the service as hardened for hostile public uploads.
- Validate extraction quality and resource ceilings against the labeled patent
  corpus before enabling access beyond the allowlisted users.

