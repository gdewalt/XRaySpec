# X-Ray Spec

A hosted, single-tenant, per-user patent **specification viewer**: upload or fetch a US patent (grant or application), extract and read the specification beside a synchronized source PDF, and cite it with printed `column:line` (grants) or paragraph (`[0042]`, applications) references. Textual `FIG. 12A` references and component reference numerals (`housing 104`) link to evidence-backed regions and exact callout boxes in the drawings.

This is a **ground-up rebuild** of an earlier Flask prototype. The extraction engine is re-implemented clean-room in a deterministic, geometry-anchored paradigm — no prototype code is reused. The full design is in **[DESIGN.md](DESIGN.md)**; this README is the map from that document to the code.

## Status

**Phase 1 complete (backend + Documents view); Phase 2 (async workers) in progress.** On top of the Phase −1 scaffold:

- §7 ORM models (User, SourceDocument, UserDocument, ExtractionArtifact, Bookmark, Annotation, AuditEvent) — `backend/app/db/models.py`
- Supabase JWT verification + email allowlist + local-user upsert — `backend/app/auth`, `backend/app/api/deps.py`
- Documents + bookmarks API with an **owner predicate on every query** and audit events — `backend/app/api/v1/documents.py`
- Async Alembic environment wired to the models — `backend/migrations/env.py`
- **Direct-upload ingestion** (§11.1): `POST /uploads` grant → browser PUT → `POST /uploads/{id}/complete` finalize (server-observed size/SHA-256/magic bytes, **no PDF parsing in the web tier**) → source/user document + queued extraction job. Storage is an interface with a Supabase impl and an in-memory impl for tests — `backend/app/storage/`, `backend/app/api/v1/uploads.py`
- **Fetch by identifier** (§11.2, §7.2): a deterministic structured patent parser (grants `B1`/`B2`/`A` **and** applications `A1`/`A2`, with ambiguous/unsupported input rejected as a validation choice) wired into `POST /documents` (fetch), which creates the source and queues a `fetching_source` job for the egress worker — `backend/app/patents/`
- **Portable import** (§11.3): a bounded, untrusted-input validator/migrator (`app/imports/`) — byte/depth/entry/bookmark/string limits, typed-locator + normalized-box validation, **v1→v2 migration**, and regeneration of all file IDs/keys — behind `POST /imports` (analyze in quarantine) → `POST /imports/{id}/commit` (explicit migration confirmation → `imported_unverified`, text-only document + immutable artifact + bookmarks). Server-side `write` added to the storage interface.
- **Deletion workflow** (§9.4): DELETE revokes access synchronously, then an **idempotent purge** (`app/services/deletion.py`) removes bookmarks, annotations, jobs, immutable artifacts (+ their blobs), and the source PDF + row (when unshared), leaving a content-free audit tombstone. Safe to retry to completion.
- **Documents view** (frontend, §16.1): React + TS SPA — sign-in, documents list with state badges + delete, and ingestion tabs (fetch / upload / import). Verified live end-to-end against the API — `frontend/src/`
- **Job execution engine** (Phase 2, §10): the durable async spine — a portable Postgres-queue claim (`SKIP LOCKED` on Postgres, serial-safe on SQLite), lease + fencing tokens + heartbeats, cooperative cancellation, a `process_one` runner, and the job control API (`GET /jobs/{id}` snapshot, cancel, retry) — `backend/app/worker/`, `backend/app/api/v1/jobs.py`
- **Restricted-egress fetch processor** (Phase 2, §11.2/§17.4): an SSRF-hardened downloader (`app/fetch/`) — https + host-allowlist + DNS→IP public-only checks **re-run on every redirect** (anti-rebinding), byte/time/redirect caps, PDF magic-byte validation — plus a Google Patents `citation_pdf_url` resolver; the `fetch_source` processor turns `fetching_source` jobs into stored PDFs. The worker routes fetch jobs through it.
- **Extraction core** (Phase 3, §12.3–12.5): the clean-room, deterministic, geometry-anchored engine (`app/extraction/`) — an abstract page/word model, line grouping by adaptive baseline, gutter line-number detection + least-squares `y→line` fit with monotonic interpolation, two-column split, and `Entry` assembly (grant `col:line` + normalized box + source text). Decoupled from the PDF library (thin pdfplumber adapter). **Per-page OCR fallback** (pypdfium2 render + Tesseract via `app/extraction/ocr.py`) feeds the *same* line-reconstruction with confidence + `extraction_method="ocr"`; pages route native-vs-OCR by text-layer presence (native / ocr / hybrid mode). Plus atomic artifact publication (§9.2) and the `extraction_processor` wired into the worker.
- Tests: auth (401/403), the **IDOR matrix** (cross-user access → 404), upload/fetch/import/delete lifecycles, the patent + portable-save validators, the worker engine + job API, the fetch adapter + SSRF guard, and the **extraction algorithm** (line grouping, detected/interpolated line numbers, reference-numeral exclusion, two-column split, OCR TSV parsing + provenance) + publication — `backend/tests/` (**113 passing**, `ruff` clean; frontend `tsc`/`vite build` verified)

**Verified live:** the fetch adapter downloads real patents from Google Patents end-to-end. A key finding — patentimages PDFs are **image-only scans** (0 text chars even on a 2025 grant), so the fetch path *requires* OCR, which is why OCR is built now. Real-patent extraction *accuracy* is measured against the labeled corpus (§19), not asserted by unit tests. Still ahead: applications (paragraphs), figure/callout mapping (§12.6–12.7), clean-text alignment (§13), and SSE progress + resume (§10.2/§10.7).

## Stack (DESIGN.md §25)

| Layer | Choice |
|---|---|
| Language | Python |
| Backend | FastAPI (async, Pydantic validation, OpenAPI) |
| Frontend | React + TypeScript SPA (Vite); types generated from the backend OpenAPI schema |
| Data access | SQLAlchemy 2.0 (async) + Alembic |
| Auth + managed services | Supabase — Auth (JWT), managed Postgres (PITR), object storage |
| PDF rendering | PDF.js (client) |
| OCR | Tesseract (pending corpus measurement; DESIGN.md §23 #2) |

**Supabase integration rule:** the FastAPI service is the *only* database client — verify Supabase JWTs, connect to Postgres by connection string. Do **not** use Supabase's client-direct / RLS / PostgREST pattern; every query carries a server-side owner predicate (DESIGN.md §17.1).

## Repository layout

```
x-ray-spec/
├── DESIGN.md              # the authoritative design
├── backend/               # FastAPI app + extraction engine + workers
│   ├── app/
│   │   ├── main.py        # FastAPI entrypoint (/live, /ready)
│   │   ├── config.py      # app settings (env)
│   │   ├── api/           # HTTP routers (DESIGN.md §14)
│   │   ├── db/            # SQLAlchemy models (DESIGN.md §7) — the store shape
│   │   ├── schemas/       # Pydantic wire types — the API shape (kept separate)
│   │   ├── auth/          # Supabase JWT verification + current-user
│   │   ├── extraction/    # CLEAN-ROOM extraction core (DESIGN.md §8, §12, §25.1)
│   │   │   ├── locator.py # typed grant/application locator (DESIGN.md §8.2)
│   │   │   ├── config.py  # versioned ExtractionConfig (cache key; §25.3)
│   │   │   ├── artifact.py# immutable artifact / entry domain model (§8)
│   │   │   └── core.py    # extract(pdf_bytes, config) -> Artifact  (pure; §25.3.1)
│   │   └── worker/        # queue claim (SKIP LOCKED) + worker runner (DESIGN.md §10)
│   └── tests/             # pytest (goldens seeded from the labeled corpus; §25.3)
├── frontend/              # React + TS SPA (Vite)
└── .github/workflows/     # CI gate (ruff + pytest + tsc/build)
```

## Foundational contracts still to define (DESIGN.md §25.3)

1. **Extraction-core interface** — `extract(pdf_bytes, config) -> Artifact`, pure, no IO/global state. *(stub in place)*
2. **Versioned config object** — every threshold/DPI in one place; it is the cache key. *(in place)*
3. **Golden harness** — seeded from the hand-labeled corpus, not prior output.
4. **Module seams + typed contracts** between core / schema / api / worker / viewer.
5. **Overlay coordinate contract** — normalized-box ↔ PDF.js viewport (scale + rotation), with a test.
6. **Test framework + CI gate** — pytest + Vitest/Playwright. *(CI stub in place)*

## Local development

Backend:

```bash
cd backend
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Copy `backend/.env.example` to `backend/.env` and fill in Supabase credentials before running against a database.
