# X-Ray Spec, a hosted Patent Specification Viewer — Product and Technical Design

Status: build plan (amended)
Last updated: 2026-09-01
Audience: implementer (solo, AI-assisted)
Implementation context: this design turns the existing Flask prototype's *concept* into a hosted, authenticated, **single-tenant, per-user** service for a known group of ~30 users. The extraction engine is **re-implemented clean-room** (§25.1); no prototype code is reused.

## 0. Revision note — scope and amendments

This revision converts the earlier "proposed target design" into an **executable build plan** and right-sizes its machinery to the actual deployment: a solo developer building with AI assistance, for ~30 known, non-adversarial users, on a modest single-provider footprint. The prototype is taken offline for the duration of the rebuild; users wait.

The product ambition is **unchanged** — the goal is to build the full capability cleanly, not to cut features. What changed is that scale-and-multi-tenant apparatus with no present user has been removed, while every security invariant and every user-facing capability is retained. Decisions settled in review:

1. **Single tenant, per-user auth.** No `Tenant`/`Membership` entities and no workspace-administrator role. Every resource is owned by a user; authorization is an ownership predicate. Multi-tenancy is added only if a second organization ever becomes real.
2. **Two document types.** Granted patents (`col:line`) **and** application publications (paragraph `[NNNN]`), modeled as one uniform entry with a **discriminated locator**.
3. **Two worker roles.** One internet-less extraction worker (native + OCR + coordination) and one restricted-egress enrichment/fetch worker. Native/OCR split into separate pools only if measured load demands it.
4. **Postgres-backed queue.** Jobs live in a table claimed with `SELECT … FOR UPDATE SKIP LOCKED`. No external broker, no transactional outbox, no dispatcher, no lost-message sweeper. Leases, fencing, heartbeats, and the reaper remain.
5. **Two checkpoint types.** (source fetched + validated + rendered) and (per-page text result). The fast downstream pipeline is recomputed on resume.
6. **Real-time progress kept.** SSE with a durable event sequence, `Last-Event-ID` resume, and pub/sub fan-out is retained.
7. **Immutable artifacts, simple re-anchor.** One active version plus the immediately-prior version for rollback. Bookmarks/annotations re-anchor by exact locator + text; non-matches are flagged for review. No fuzzy migration service and no side-by-side comparison UI in v1.
8. **Citation: presets + toggles.** Built-in profiles (grant and application variants) plus structured formatting toggles. The user-authored template DSL (parser/AST/dual renderer) is deferred.
9. **Full per-line clean-text alignment kept.** Comfortable in-place reading of scanned patents is a core need, so alignment, identity gates, and the source/display diff remain.
10. **Minimal hosting footprint.** App + two workers + managed Postgres (with PITR) + object bucket, single region, best-effort availability. Autoscaling pools, KMS key ceremony beyond provider defaults, and rehearsed DR drills as a launch gate are dropped. Worker isolation and the egress boundary stay.
11. **Accessibility by construction; audit right-sized.** Build every screen accessibly from day one (including the non-canvas Callouts list); a formal multi-AT audit is scoped to actual assistive-technology users (none known today).
12. **Callout linking: simple association + chooser.** Ship callout detection, mention detection, highlighting, and a *simple* association (exact value + same-figure context) with an ambiguous-candidate chooser. The calibrated multi-signal ranker is deferred; it is a pure algorithm upgrade behind stored candidates.
13. **Limits: safety caps only.** Keep every per-document and per-artifact resource-safety cap plus one global concurrency constant. Drop fair scheduling, admission control, and daily budgets.
14. **Clean-room extraction.** The prior `app.py` is discarded, not reused. Extraction is re-implemented from scratch in the same **deterministic, geometry-anchored paradigm** (§12, §25.1) — not a model-based/VLM paradigm. Models may propose, geometry must confirm, and source text is never overwritten by a model. Consequence: extraction quality is entirely net-new and validated in Phase 3.
15. **Technology stack.** Python · FastAPI · React + TypeScript SPA · SQLAlchemy 2.0 + Alembic · Supabase (Auth + managed Postgres + object storage). See §25.

One decision remains open: the **OCR engine** (measure Tesseract on the scanned corpus before committing; a cloud OCR API would trade away privacy and worker isolation and is disallowed without a separate review). The hosting **provider** is now Supabase (§25.2).

The infra-first phase order (§21) is retained by choice; the residual risk it creates is that extraction, callout, and alignment quality — now known to be central to these users and re-implemented entirely from scratch — are validated only in Phase 3.

## 1. Executive summary

X-Ray Spec is a hosted service for extracting, reading, navigating, and citing United States patent specifications. Users upload a patent PDF, fetch a public patent by identifier, or import a portable extraction. The service reconstructs printed citation references — printed `column:line` for granted patents and paragraph numbers for application publications — selectively uses OCR for weak or image-only pages, optionally aligns noisy text with a verified Google Patents record, and presents the result beside a synchronized source PDF. It turns textual figure references such as `FIG. 12A` into source links and connects component reference numerals such as `housing 104` to the matching `104` callout on the appropriate drawing, where the callout is highlighted.

The product is designed around five foundations:

1. **Private, per-user documents.** Authentication and per-user authorization protect every source, artifact, job, PDF, bookmark, citation profile, and export. Opaque identifiers supplement authorization; they never replace it.
2. **Durable asynchronous work.** PostgreSQL is the system of record *and* the job queue, private object storage holds PDFs and immutable artifacts, and isolated workers consume jobs at least once. Jobs support real stages, durable progress, cancellation, retry, and checkpoint-based resumption.
3. **Auditable extraction.** Raw PDF/OCR text is never overwritten. Every displayed line records its page, box, locator, extraction method, alignment method, reference method, confidence, and warnings. Inferred mappings are visibly qualified.
4. **Safe extensibility.** Portable saves are validated against a versioned schema and rendered without HTML concatenation. Citation formats use built-in profiles plus structured formatting options — never executable templates or HTML.
5. **Graceful degradation.** Google enrichment, figure mapping, callout linking, and source synchronization may fail independently. A useful text artifact can complete with warnings without presenting partial or inferred results as authoritative.

The target is a modular product with a small number of purposeful deployment boundaries: a stateless API, a native/OCR extraction worker, a restricted enrichment adapter, durable stores, and operational roles (reaper and janitor) that may run in-process. The design does not require a broad microservice decomposition, a message broker, or multi-tenant infrastructure.

## 2. Scope and product principles

### 2.1 Goals

- Provide a secure hosted workspace for patent PDFs and extracted specifications for a known group of users.
- Support born-digital, scanned, and hybrid US patent grants **and** application publications.
- Preserve the source document and its native citation system — printed `column:line` for grants, paragraph numbers for applications.
- Highlight figure references and reference-numeral mentions in specification text and link them to evidence-backed figure regions and callout boxes in the source drawings.
- Make extraction uncertainty visible and traceable to the source PDF.
- Support real-time, reconnectable extraction progress and reliable recovery from worker or browser interruption.
- Let users select from useful citation defaults and configure them with safe structured options.
- Make search, source inspection, navigation, bookmarking, annotation, export, reprocessing, and deletion predictable across sessions and devices.
- Operate safely under untrusted PDFs, crafted imports, external-provider failures, and worker restarts.

### 2.2 Non-goals for the first hosted release

- Legal advice or a guarantee that extracted text is legally authoritative.
- Editing the source PDF or writing annotations into it.
- Full patent discovery by semantic search, assignee, or prior-art graph.
- Multi-tenant / multi-organization isolation, workspace administration, and per-tenant fair scheduling.
- Non-US publication layouts unless separately validated and enabled.
- Design (`S`) and plant (`P`) patents at launch.
- Real-time multi-user co-editing.
- User-authored citation template languages, plugins, JavaScript, HTML, or server-side Jinja.
- A user-authored citation **template DSL** (deferred; see §15).
- A fuzzy bookmark/annotation migration service and a side-by-side artifact comparison UI (deferred; see §7.1).
- Treating server retention as a permanent archive unless the operator has explicitly configured that policy.

### 2.3 Design principles

- **Source before polish:** PDF/OCR text and geometry remain available even when clean external text is displayed.
- **Uncertainty is data:** confidence, provenance, gaps, and inference methods are persisted and shown.
- **The database decides:** the job queue, cache entries, and progress broadcasts all live in or are reconstructable from PostgreSQL; caches and pub/sub are accelerators, not authoritative state.
- **Immutable results, mutable user state:** extraction artifacts are versioned and immutable; bookmarks, annotations, active-version choices, and citation preferences are separate.
- **Partial capabilities beat false success:** optional enrichment may degrade; a questionable citation must not be silently labeled exact.
- **No shared filesystem coordination:** web and worker replicas communicate through PostgreSQL and object storage, not process-local locks or temp-file sidecars.
- **Bound every expensive operation:** bytes, pages, pixels, CPU, memory, wall time, redirects, retries, and concurrency all have explicit limits.
- **Right-size to real scale:** build the full capability, but do not build scale-and-multi-tenant machinery for users who do not exist. Keep every security and correctness invariant regardless of scale.
- **Preserve workflows, improve interactions:** the successful upload/read/cite workflow is retained while timer-based processing, overloaded clicks, unsafe rendering, and incomplete history are deliberately replaced.

## 3. Users and primary journeys

### 3.1 Roles

| Role | Capabilities |
|---|---|
| Member (user) | Create and view their own documents, run extraction, bookmark, annotate, manage personal citation profiles, export, and delete documents they own. Every user owns their documents; there is no shared workspace. |
| Service operator | Operate infrastructure and inspect content-free diagnostics. Configure instance-level policy (default/max retention, whether external enrichment is enabled). Document-content access is not implicit and requires a separately audited support mechanism. |

There is no `Tenant` or workspace-administrator role. All data access is **per-user owner-scoped**. Instance-wide policy (retention defaults, enrichment enablement, quotas) is operator configuration, not a per-tenant entity. Sharing between users is out of scope for the first release; if added later, additional owner/sharing predicates layer over the same ownership model.

### 3.2 Primary journeys

#### Upload and process

1. The user chooses a PDF and sees size, privacy policy, retention, and whether external enrichment is enabled.
2. The browser uploads directly to a single-purpose private object-storage grant.
3. The API finalizes the upload, verifies size/hash, creates a source and user document, and queues extraction.
4. The user sees server-reported stages and page-based progress, may leave and return, and may cancel.
5. Completion opens the viewer or reports `complete_with_warnings` with actionable explanations.

#### Fetch a public patent

1. The user enters a canonical or human-formatted US patent identifier (grant or application).
2. The API validates country, number, and kind and classifies the document as a grant or an application, rather than stripping all non-digits.
3. A restricted fetch adapter resolves and downloads the PDF under host, redirect, byte, and timeout limits.
4. The same durable extraction workflow runs.

#### Resume, retry, or reprocess

- Reopening the Documents page restores the authoritative job snapshot and reconnects to progress events.
- Resume continues a cancelled or interrupted job from the verified compatible page-text checkpoints.
- Retry creates a new attempt with the same engine/configuration and reuses safe checkpoints.
- Reprocess creates a new job when options, engine version, artifact schema, or enrichment policy changes. The prior active artifact remains available until retention or explicit deletion removes it.

#### Read and cite

1. The user searches or navigates to a specification line, claim, heading, figure, reference numeral, bookmark, or deep link.
2. The viewer shows display text with visible quality indicators and allows raw-versus-aligned comparison.
3. A distinct View source action opens and synchronizes the PDF. Selecting `FIG. 12A` centers the mapped figure region; selecting a linked reference numeral such as `104` centers and highlights that exact callout on the selected figure.
4. Selection opens a copy/citation control. The user chooses a system or personal citation profile, previews the result, and copies display text, source text, citation only, or quote plus citation.

#### Manage documents

The Documents view presents one coherent list with source, status, active artifact, extraction mode, warnings, last opened time, expiry, storage usage, and actions for open, resume, retry, reprocess, export, and delete.

## 4. Service objectives and quality attributes

Initial numerical targets are release objectives and must be recalibrated using corpus and load-test evidence before launch. Availability is **best-effort, single-region**; users tolerate planned downtime and the tool is not mission-critical.

| Area | Initial target |
|---|---|
| API availability | Best-effort, single region. No formal SLA; planned maintenance is acceptable. |
| API responsiveness | p95 under 500 ms for metadata operations under expected (low) load; large blob transfer excluded. |
| Progress freshness | p95 event visibility within 2 seconds of durable stage/page update (SSE; polling fallback). |
| Reconnectability | A browser reconstructs job state using `GET /api/v1/jobs/{id}` and resumes SSE using `Last-Event-ID`. |
| Cancellation | Request acknowledged immediately; active parser/OCR process terminated within 15 seconds p95 at a cancellation checkpoint. |
| Job durability | No acknowledged job is lost after an API or worker restart. |
| Artifact publication | A viewer sees the previous ready artifact or a fully validated new artifact, never a partially published result. |
| Authorization | Zero cross-user resource access in authorization (IDOR) tests. |
| Deletion | Access revoked synchronously; live blobs and derived data deleted within the stated deletion window, with backup expiry disclosed separately. |
| Accessibility | Accessible by construction (semantic, keyboard-complete, non-canvas Callouts alternative). Formal multi-AT audit scoped to actual assistive-technology users. |
| Data durability | Managed PostgreSQL point-in-time recovery (PITR) is retained as the primary durability guarantee. |

Extraction accuracy objectives are defined in Section 19 and are measured separately for high-confidence and overall coverage.

## 5. Hosted architecture

```text
Browser
  ├─ OIDC login and secure application session
  ├─ HTTPS JSON API
  ├─ resumable SSE progress stream
  ├─ single-purpose private upload grant
  └─ authorized PDF range requests
          │
          v
Edge / load balancer (provider-managed)
          │
          v
Stateless Web/API service
  ├─ authentication and per-user authorization
  ├─ document, job, citation, bookmark, export APIs
  ├─ request validation, safety limits, idempotency
  ├─ progress snapshots and SSE
  └─ authorized artifact/PDF delivery
          │
          ├──────────── PostgreSQL
          │              system of record: ownership, jobs (queue),
          │              leases, events, quality summaries, user state
          │
          └──────────── Private object storage
                         source PDFs, page checkpoints, artifacts, exports
          │
          │  jobs claimed directly from PostgreSQL (SELECT … FOR UPDATE SKIP LOCKED)
          │
   ┌──────┴───────────────────────┐
   v                              v
Extraction worker            Enrichment/fetch worker
native + OCR + coordinator   restricted internet egress
no internet                  (Google Patents + patent images)
   │                              │
   └───────────── in-process reaper / janitor ────────────┘
```

### 5.1 Architectural decisions

- PostgreSQL is authoritative for lifecycle, authorization, **and job queuing**. Inserting a job row *is* the enqueue; there is no separate broker and therefore no dual-write and no outbox.
- Private object storage is authoritative for document and artifact bytes.
- Job claiming is at least once (a worker can crash after claiming); worker code and publication are idempotent, guarded by leases and fencing tokens.
- The web tier does not parse PDFs or execute OCR.
- A **single extraction worker role** runs native extraction, OCR, and coordination in one process. Native and OCR are separate *modules* and may be split into independently scaled pools later, but this is a deployment change, not a rewrite. It is not done until measured load requires it.
- The extraction worker has **no outbound internet access**. Google Patents access runs in a **separate restricted-egress enrichment/fetch worker**. This separation is a security invariant (untrusted-PDF isolation and SSRF containment), independent of scale.
- A modular monolith is appropriate for API and domain code. Deployment roles are separated only for the security/egress boundary, not to create unnecessary services.
- Browser-visible artifact content is obtained from authenticated JSON endpoints rather than embedded in executable inline script contexts.

### 5.2 Trust boundaries

| Boundary | Untrusted material | Required controls |
|---|---|---|
| Browser → edge/API | Requests, identifiers, imported data | OIDC, secure session, CSRF and Origin checks, schema validation, byte/rate limits, per-user authorization, CSP. |
| Browser → object storage | Source uploads | Expiring single-purpose grants with server-selected keys, fixed byte ceiling, restricted method/content type, and finalization verification. |
| API → database/object store | User and object identifiers | Server-generated keys, ownership predicates on every query, least-privilege credentials, private buckets, encryption. |
| Worker → parser/OCR process | Potentially malicious PDFs and images | Rootless isolation, read-only image, private scratch, no secrets/network, CPU/memory/PID/disk/pixel/time limits, killable process group. |
| Worker output → viewer | PDF/OCR/provider strings and imported fields | Strict artifact schema, plain-text rendering, DOM properties/`textContent`/`dataset`, no untrusted HTML construction. |
| Enrichment worker → internet | Redirects, changed HTML, malicious responses | Scheme/host/IP allowlist after every redirect, redirect cap, byte/time limits, bounded retries, circuit breaker, fixture-tested parser. |
| Operator plane | Privileged actions and diagnostics | MFA, separate role, least privilege, audited access, no document-text logging. |

## 6. Component responsibilities

| Component | Responsibility |
|---|---|
| Edge | TLS termination, coarse rate limiting, request-size enforcement, and routing (provider-managed). |
| Web/API | Identity/session handling, per-user authorization, resource APIs, validation, idempotency, safety-limit decisions, progress delivery, and authorized blob streaming. |
| Upload finalizer | Verify uploaded object key, size, SHA-256, magic bytes, and user ownership before creating a source document. |
| Extraction worker | Claim jobs from PostgreSQL; preflight PDFs; extract native words; classify pages; render and OCR selected pages under strict resource limits; reconstruct references (per document type); map figures; detect drawing callouts; associate textual reference numerals; assemble the quality report; and publish an immutable artifact. Native, OCR, and coordination are internal modules of one internet-less process. |
| Enrichment/fetch worker | Under restricted egress: download source PDFs by identifier; fetch allowlisted Google Patents data; verify identity; create an enrichment snapshot; and supply clean section text. |
| Citation engine | Render built-in and configured citation profiles to plain text (grant and application locator forms), and version profiles. An API module. |
| PostgreSQL | Authoritative metadata, ownership, policies, **the job queue**, attempts, leases, progress events, mutable user state, and audit records. |
| Object storage | Private immutable PDFs, page-text results, manifests, artifacts, and exports; temporary staging uses a separately expired prefix. |
| Reaper | Detect expired leases and safely make eligible jobs claimable again. May run in-process. |
| Janitor | Remove expired checkpoints, staged/orphaned objects, expired exports, and deletable retained data. May run in-process. |

There is no separate managed queue, dispatcher, or transactional-outbox processor: the job table is the queue.

## 7. Durable domain model

Immutable extraction data is separated from mutable user and job state. There are no `Tenant` or `Membership` entities.

| Entity | Key fields and responsibility |
|---|---|
| `User` | Identity-provider subject, account state, and per-user preferences (default citation profile, theme/density). |
| `SourceDocument` | Owner (user), source type, private PDF object key, SHA-256, bytes, pages, canonical patent identity (grant or application), original filename, fetch/upload provenance, and lifecycle state. |
| `UserDocument` | User-facing title, source relationship, current active artifact, owner, last-opened time, explicit expiry, and document state. |
| `ExtractionJob` | Source/document, owner, requested engine/config, status, stage, progress snapshot, cancellation flag, idempotency record, failure code, lease/fencing fields, and timestamps. Rows in this table are the job queue. |
| `JobAttempt` | Attempt number, worker, fencing token, lease/heartbeat, retry reason, start/end, and resource summary. |
| `StageCheckpoint` | Source hash, engine/config/schema versions, checkpoint type (`source_ready` or `page_text`), page key where applicable, object hash/key, validation state, and expiry. Only two checkpoint types exist (see §10.7). |
| `ExtractionArtifact` | Immutable source/config identity, artifact schema, manifest object, quality disposition/summary, enrichment snapshot, `active` flag, `superseded` flag, and ready timestamp. |
| `EnrichmentSnapshot` | Provider, canonical URL, fetch time, content hashes, parsed metadata, section-text hashes, and identity-match evidence. |
| `Bookmark` | User, document, stable entry target, label, color/tags, and timestamps. |
| `Annotation` | User, document/artifact target, range, note, and timestamps. |
| `ReferenceLinkOverride` | User-reviewed choice connecting an ambiguous text mention to a callout occurrence. Mutable user state; never rewrites immutable extraction evidence. |
| `CitationProfile` | Scope (`system` or `user`), name, base format, formatting options (structured, not a template string), copy behavior, version, and status. |
| `ExportSnapshot` | Document/artifact/profile versions, object key/hash, format, expiry, and creator. |
| `AuditEvent` | Actor, action, resource class/ID, outcome, correlation ID, timestamp, and content-free metadata. |

### 7.1 Relationships and immutability

- A `SourceDocument` has many extraction jobs and artifacts, all owned by one user.
- A `UserDocument` points to one source and one currently active ready artifact.
- Reprocessing creates a new job and immutable artifact; it never mutates an older artifact.
- **Active version and rollback:** a document keeps its current active artifact plus the immediately-prior ready artifact for rollback (a failed reprocess leaves the prior active artifact in place). Older superseded artifacts are eligible for retention cleanup. There is no long-lived multi-version retention requirement.
- **Bookmark/annotation re-anchoring (simple):** bookmarks and annotations target a stable artifact entry ID. When the active artifact changes, targets re-anchor by **exact locator + text match** — anything on an unchanged line survives silently. Anything that no longer matches is **flagged "review / re-place"** for the user; it is never silently rewritten. There is no fuzzy two-artifact migration service and no side-by-side comparison UI in v1.
- Figure/reference-numeral corrections are stored as `ReferenceLinkOverride` records layered over the artifact; they never mutate detected callouts, candidates, or provenance.
- Citation profiles are versioned. An export records the exact profile version used.
- Cross-user deduplication is disabled to avoid authorization and timing side channels. Per-user cache reuse may key on source hash + engine version + config hash + artifact schema.
- Large entry arrays and page results are immutable blobs. PostgreSQL stores queryable summaries and object references, not thousands of line rows unless a private per-user search index is later required.

### 7.2 Canonical patent identity

Patent identity is parsed into structured fields, and the parser distinguishes **grants** from **application publications**, which have different numbering systems.

Granted patent:

```json
{
  "doc_type": "grant",
  "country": "US",
  "number": "12262260",
  "kind": "B2",
  "canonical": "US12262260B2",
  "display": "US 12,262,260 B2"
}
```

Application publication:

```json
{
  "doc_type": "application",
  "country": "US",
  "number": "20240123456",
  "kind": "A1",
  "canonical": "US20240123456A1",
  "display": "US 2024/0123456 A1"
}
```

Supported kinds at launch: granted utility patents (`B1`, `B2`, and the pre-2001 `A` grant) and application publications (`A1`, `A2`). Design (`S`) and plant (`P`) patents are out of scope.

The parser recognizes separators, country prefixes, valid kind suffixes, and the year-prefixed application numbering. It never retains a kind suffix as part of the patent number. Ambiguous input produces a validation choice rather than a guessed fetch.

## 8. Versioned extraction artifact

An artifact is immutable and self-describing. Its manifest contains schema, source/config identity, section/page summaries, references to entry/page blobs, figure mappings, cover metadata, quality, and warnings.

### 8.1 Manifest sketch

```json
{
  "schema_version": 2,
  "artifact_id": "art_...",
  "source": {
    "sha256": "...",
    "patent": {"canonical": "US12262260B2", "doc_type": "grant"},
    "page_count": 42
  },
  "engine": {
    "version": "...",
    "config_hash": "...",
    "pdf_parser": "...",
    "ocr_engine": "...",
    "ocr_languages": ["eng"]
  },
  "mode": "hybrid",
  "disposition": "complete_with_warnings",
  "entries_object": {"key": "...", "sha256": "...", "encoding": "json+gzip"},
  "page_results": [],
  "figure_index_object": {"key": "...", "sha256": "..."},
  "callout_index_object": {"key": "...", "sha256": "..."},
  "reference_mentions_object": {"key": "...", "sha256": "..."},
  "cover": {},
  "quality": {},
  "warnings": []
}
```

Object keys are never accepted from imported files or exposed as authorization credentials.

### 8.2 Line entry contract

Every entry is a line/span with a page box (so highlighting and jump-to-line work identically for both document types), plus a **typed locator** that varies by document type.

```json
{
  "entry_id": "line_0000123",
  "ordinal": 123,
  "page_index": 6,
  "locator": {"kind": "grant", "column": 3, "printed_line": 15},
  "box": [0.0812, 0.2241, 0.4410, 0.2387],
  "source_text": "A raw PDF or OCR line",
  "display_text": "A verified or aligned display line",
  "structure": {
    "paragraph_start": false,
    "split_word_end": false,
    "section": "description"
  },
  "provenance": {
    "extraction": {"method": "native", "candidate_score": 0.98, "ocr_confidence": null},
    "alignment": {"method": "fuzzy", "provider": "google_patents", "score": 0.93, "identity_verified": true},
    "reference": {"method": "interpolated", "anchor_count": 9, "fit_error": 0.41, "layout_borrowed": false}
  },
  "confidence": {"text": "medium", "reference": "high"},
  "warnings": ["display_text_fuzzy_alignment"]
}
```

For an application publication, the locator is paragraph-based:

```json
{"locator": {"kind": "application", "paragraph": "0042"}}
```

Rules:

- The `locator` is a discriminated type: `{kind: "grant", column, printed_line}` or `{kind: "application", paragraph}`. A rendered `ref` string (e.g. `"3:15"` or `"[0042]"`) is a *view* of the locator, never the primitive.
- `source_text` is never overwritten by external text.
- `display_text` equals `source_text` when no accepted transformation exists.
- `page_index` is mandatory even if a locator also exists.
- Boxes are finite normalized top-left coordinates with `0 <= x0 < x1 <= 1` and `0 <= y0 < y1 <= 1`.
- `entry_id` is stable within an artifact and is used for deep links, bookmarks, and annotations. Rendered `ref` is not assumed unique.
- Confidence categories derive from a versioned quality policy; underlying metrics remain available.
- A viewer may display cleaned text but source copying and comparison always remain possible.

### 8.3 Figure mapping contract

Figure navigation uses four distinct terms:

- **Figure reference:** text such as `FIG. 3`, `FIGS. 4–6`, or `Figure 12A` in the specification.
- **Figure region:** the page and optional normalized panel box containing one figure.
- **Reference-numeral mention:** an inline component identifier such as the `104` in `housing 104`.
- **Callout occurrence:** the printed `104` label at a specific bounding box inside a drawing figure.

A figure reference links to a figure region. A reference-numeral mention links to one or more callout occurrences, preferably restricted to the figure or figure set being discussed in the surrounding text. The figure/callout system applies unchanged to grants and applications.

The UI may use the friendlier phrase **reference number**; the artifact uses **reference numeral** for the number mentioned in prose and **callout occurrence** for its visible label on a drawing.

```json
{
  "figure_record_id": "figure_12A",
  "figure_id": "12A",
  "status": "probable",
  "occurrences": [
    {
      "figure_occurrence_id": "figocc_000031",
      "page_index": 3,
      "region_box": [0.04, 0.08, 0.96, 0.89],
      "label_box": [0.43, 0.90, 0.57, 0.94],
      "rotation": 0,
      "method": "detected_rotated_ocr",
      "confidence": "medium",
      "score": 0.88,
      "evidence": ["sheet_marker", "label_match"]
    }
  ],
  "warnings": []
}
```

Each textual figure-reference mention is stored separately from the destination figure so one range/list can resolve to several figures:

```json
{
  "mention_id": "figmention_000117",
  "entry_id": "line_0000118",
  "raw_text": "FIGS. 12A–12C",
  "display_span": {"start": 4, "end": 17, "unit": "unicode_code_point"},
  "source_span": {"start": 4, "end": 17, "unit": "unicode_code_point"},
  "expanded_figure_ids": ["12A", "12B", "12C"],
  "status": "verified",
  "destinations": [
    {"figure_id": "12A", "figure_occurrence_id": "figocc_000031", "page_index": 3, "score": 0.99},
    {"figure_id": "12B", "figure_occurrence_id": "figocc_000032", "page_index": 3, "score": 0.99},
    {"figure_id": "12C", "figure_occurrence_id": "figocc_000033", "page_index": 4, "score": 0.98}
  ],
  "warnings": []
}
```

A figure record may have multiple occurrences when a figure is repeated or spans sheets. `region_box` and `label_box` are independently optional: when the label is located but the panel boundary is uncertain, the viewer centers/highlights the label and does not invent a full figure region.

Sequential, nearest-number, or base-figure inference is always labeled `inferred`, cannot become evidence for subsequent inference, and is never rendered as a high-confidence exact link. The UI may require confirmation before following a low-confidence figure mapping.

### 8.4 Reference-numeral and callout contracts

Callout detection records every evidence-backed occurrence of a numeral on a drawing rather than reducing a numeral to one page:

```json
{
  "callout_id": "callout_000482",
  "value": "104",
  "raw_label": "104",
  "page_index": 3,
  "figure_id": "12A",
  "figure_occurrence_id": "figocc_000031",
  "box": [0.6221, 0.4130, 0.6584, 0.4388],
  "rotation": 0,
  "method": "sparse_ocr",
  "confidence": "high",
  "score": 0.97,
  "warnings": []
}
```

Text analysis records a half-open span for each reference-numeral mention and its ranked destination candidates:

```json
{
  "mention_id": "mention_000921",
  "entry_id": "line_0000123",
  "value": "104",
  "component_label": "housing",
  "display_span": {"start": 18, "end": 21, "unit": "unicode_code_point"},
  "source_span": {"start": 17, "end": 20, "unit": "unicode_code_point"},
  "figure_context": ["12A"],
  "status": "ambiguous",
  "selected_callout_ids": [],
  "candidates": [
    {
      "callout_id": "callout_000482",
      "score": 0.9,
      "evidence": ["same_figure_context", "exact_value"]
    }
  ],
  "warnings": []
}
```

Rules:

- A numeral may have many callout occurrences across figures; the index preserves them all.
- **v1 association is simple** (see §12.7): exact normalized value plus same-figure context. `verified` requires one clearly dominant destination under that rule supported by figure context; if the same numeral appears more than once inside that figure, all matching occurrences may be selected and highlighted together.
- `probable` may provide a default plus alternatives. `ambiguous` shows a candidate chooser and has no silent default. `unresolved` remains readable text without a source link. The candidates array and scores are stored so a later calibrated ranker (§12.7) can improve selection without a schema change.
- Text spans are validated against the exact numeral in each text stream. Alignment can change offsets, so source and display spans are stored separately.
- Numeric quantities, dates, dimensions, claim numbers, patent numbers, page/line/paragraph numbers, and figure identifiers are excluded unless contextual evidence identifies them as component reference numerals.
- User corrections create `ReferenceLinkOverride` records; they never change immutable candidates or confidence.
- Callout boxes are normalized and validated like line boxes. Drawing-page coordinates remain the source of the highlight at every PDF zoom level.

### 8.5 Quality report

The artifact quality report includes:

- pages classified and pages included in the specification;
- native, OCR, and unresolved page counts;
- for grants, expected-column continuity and gaps; for applications, paragraph-number continuity and gaps;
- gutter/paragraph anchor counts, fit residuals, synthetic or borrowed references;
- duplicate/non-monotonic reference counts;
- source/display character coverage and exact/fuzzy/unmatched alignment distribution;
- identity verification status;
- high/medium/low confidence entry counts;
- detected and inferred figure counts;
- figure-region coverage, callout detection counts, linked/ambiguous/unresolved reference-numeral mentions, and association confidence distribution;
- incomplete sections/pages and user-visible warnings;
- elapsed time and resource summary without document content.

The aggregate disposition is `complete`, `complete_with_warnings`, `partial`, or `failed`. `Partial` means a probable specification page is unresolved; it cannot be silently promoted to complete. A single opaque quality score is not sufficient for auditability.

## 9. Storage, publication, retention, and deletion

### 9.1 Storage responsibilities

- PostgreSQL stores users, authorization relationships (ownership), documents, **jobs (the queue)**, leases, events, quality summaries, policies, and mutable viewer state.
- Private object storage stores source PDFs, validated page-text checkpoints, immutable artifacts, and exports.
- An optional cache/pub-sub layer can reduce latency (SSE fan-out) but is disposable.
- No correctness or lifecycle operation relies on a shared local filesystem.

### 9.2 Atomic artifact publication

1. A worker writes page results and the final manifest under a job-attempt staging prefix.
2. Every object is hashed and read-back or metadata-verified.
3. Output schema and cross-object references are validated.
4. Immutable ready objects are copied/promoted to deterministic final keys when necessary.
5. In one database transaction, the worker inserts the artifact, marks it ready and active (demoting the prior active artifact to rollback), updates job success, and updates the user document’s active artifact — all guarded by the current fencing token.
6. Only ready, active or rollback artifacts are returned by the API.
7. A janitor deletes abandoned staging objects and superseded artifacts (beyond the retained rollback) after a documented grace period.

If publication loses its lease/fencing check, it cannot commit. A stale worker cannot overwrite a newer attempt. Reprocessing failure leaves the prior ready artifact active.

### 9.3 Retention

- Every user document exposes `retention_policy` and `expires_at`.
- Instance policy (operator configuration) defines default/max retention and whether users may pin a document.
- The Documents UI shows expiry and storage implications before processing and export.
- Checkpoints have a shorter explicit TTL than ready artifacts unless retained for an active resumable job.
- Exports expire independently.
- Retention enforcement runs continuously through scheduled durable work, not only at process startup.

### 9.4 Deletion

1. The API authorizes the request (owner check) and immediately makes the user document inaccessible.
2. A deletion workflow removes bookmarks, annotations, reference-link overrides, exports, job checkpoints, artifacts, and source objects when reference counts and instance policy permit.
3. Blob deletion is idempotent and retried until complete.
4. A content-free tombstone and audit event record completion.
5. Object-version and backup expiry are disclosed separately; the product does not promise instantaneous erasure from immutable backups.

## 10. Asynchronous job model

### 10.1 Lifecycle and stage

Lifecycle status and work stage are distinct.

**Status:** `queued`, `running`, `cancelling`, `succeeded`, `failed`, `cancelled`.

**Stage:**

1. `validating_source`
2. `fetching_source` when applicable
3. `classifying_pages`
4. `extracting_native`
5. `extracting_ocr`
6. `reconstructing_references`
7. `mapping_figures`
8. `mapping_reference_numerals`
9. `enriching`
10. `aligning_text`
11. `quality_review`
12. `persisting_artifact`

Successful lifecycle status may yield artifact disposition `complete`, `complete_with_warnings`, or explicitly `partial` if instance policy permits publishing a usable but incomplete artifact.

`mapping_figures` reports drawing pages/labels/regions analyzed. `mapping_reference_numerals` has durable substages `detecting_drawing_callouts`, `detecting_text_mentions`, `resolving_mention_targets`, and `validating_figure_links`; progress uses drawing pages, entries, and mentions as real work units.

### 10.2 Progress events

Progress is generated by the server from durable work units, not from an elapsed-time guess. Real-time delivery uses SSE with a durable, resumable event sequence and optional pub/sub fan-out; polling is the fallback.

```json
{
  "sequence": 184,
  "job_id": "job_...",
  "status": "running",
  "stage": "extracting_ocr",
  "stage_label": "Reading scanned pages",
  "completed_units": 12,
  "total_units": 18,
  "unit": "pages",
  "overall_fraction": 0.64,
  "current_page": 27,
  "indeterminate": false,
  "warnings": [],
  "occurred_at": "2026-09-01T17:00:00Z"
}
```

- Events have durable, monotonically increasing sequence numbers.
- `GET /api/v1/jobs/{id}` returns the authoritative current snapshot.
- `GET /api/v1/jobs/{id}/events` uses SSE and honors `Last-Event-ID`.
- Polling the job endpoint is the fallback when SSE is blocked.
- Pub/sub can wake SSE handlers but is not required to reconstruct state.
- Stage transitions and meaningful page/work-unit increments are durable; high-frequency telemetry may remain metrics-only.
- Overall progress is monotonic. When the denominator is unknown it is `null`/indeterminate, not an invented percentage.
- Classification freezes the page work plan and stage weights used for an overall percentage. If later validation adds OCR work, the server may extend remaining work but must not move the displayed percentage backward; the stage counters remain the precise source of truth.
- The UI may estimate an ETA only when the server supplies one based on observed page throughput. It never substitutes a client timer for progress.

### 10.3 Idempotency and delivery

- Job-creating requests require `Idempotency-Key`.
- Uniqueness is user + operation + key; the stored request digest prevents using one key for different inputs.
- Repeating a key with the same digest returns the original resource; a conflicting digest returns `409`.
- **The job table is the queue.** Committing the job row *is* the enqueue — one transaction, no external broker, no outbox, no dispatcher. Workers claim with `SELECT … FOR UPDATE SKIP LOCKED` against ready rows.
- Claiming is at least once in the sense that a worker may crash after claiming; the lease/reaper mechanism (§10.4) makes such a job claimable again, and publication idempotency prevents double effects.
- Work outputs use deterministic keys derived from source hash, engine version, configuration hash, artifact schema, stage, and page/work-unit.

### 10.4 Leases and fencing

- A worker claims a job in a database transaction and receives a fencing token and attempt number.
- It renews a short lease with heartbeats.
- Every checkpoint write and terminal commit includes the current fencing token/attempt.
- An expired/stale worker cannot publish or mutate current state.
- A reaper makes eligible work claimable after bounded backoff.
- Deployment drains stop new claims and let in-flight workers checkpoint or relinquish leases.

### 10.5 Cancellation

- Cancellation is an idempotent persisted request.
- Workers check it between pages, before expensive stages, and while supervising parser/OCR subprocesses.
- Active child process groups are terminated, then force-killed after a short grace period.
- If artifact publication committed before cancellation won, success remains authoritative; otherwise the job becomes `cancelled`.
- Cancelled jobs never expose partial artifacts as complete.
- Verified page-text checkpoints may remain for a visible, documented resumability period unless the user deletes the document.

### 10.6 Retry, resume, and reprocess

- **Automatic retry:** same job, new attempt, only for transient storage failures, worker loss, and eligible upstream 429/5xx/network errors. Backoff is capped and jittered.
- **Retry:** user requests another attempt with unchanged source/engine/config; verified compatible page-text checkpoints may be reused.
- **Resume:** a cancelled/interrupted job continues from the first absent or invalid page-text checkpoint with unchanged source/engine/config/schema; the fast downstream pipeline is recomputed.
- **Reprocess:** creates a new job when options, engine, enrichment policy, or schema changes. It never mutates the prior artifact.
- Malformed, encrypted, unsupported, limit-exceeding, identity-mismatch, and deterministic parser failures are not blindly retried.

### 10.7 Checkpoint contract

Checkpointing is concentrated where recomputation is genuinely expensive: OCR. Only **two checkpoint types** exist:

1. `source_ready` — the source PDF has been fetched, validated, and rendered/preflighted (page inventory, dimensions, rotation, page classification inputs).
2. `page_text` — one page's text result (native or OCR), including word coordinates and confidence, keyed by page.

On resume: verify `source_ready`; then for each specification page, reuse the `page_text` checkpoint if present or (re-)extract/OCR the missing page; then **recompute the entire fast downstream pipeline fresh** — reference reconstruction, figure mapping, callout detection, text-mention extraction, association, alignment, and quality review. These stages run in seconds over page data, so recomputing them on every resume is cheaper than persisting and version-verifying them, and it guarantees the downstream result always matches the current engine/config.

Each checkpoint is keyed by source hash, artifact schema, engine manifest, configuration hash, and (for `page_text`) page index. Resume verifies every dependency before reuse. A changed OCR engine invalidates `page_text` checkpoints for OCR'd pages but need not invalidate an immutable source hash or compatible `source_ready` preflight. Checkpoint retention is explicit and checkpoints are deleted with the document.

### 10.8 Failure taxonomy

Public error responses use stable codes and recovery actions:

| Code | Retry class | Typical recovery |
|---|---|---|
| `INVALID_PDF` | Terminal | Upload a valid PDF. |
| `ENCRYPTED_PDF` | Terminal in first release | Upload an unencrypted copy. |
| `SOURCE_LIMIT_EXCEEDED` | Terminal | Use a supported-size document. |
| `UNSUPPORTED_PATENT_LAYOUT` | User choice | Retry OCR or retain an explicitly partial diagnostic if policy permits. |
| `OCR_UNAVAILABLE` | Operator/transient | Retry after readiness is restored. |
| `OCR_PAGE_TIMEOUT` | User/automatic policy | Resume with adjusted settings or accept a warning if usable. |
| `UPSTREAM_RATE_LIMITED` | Transient | Automatic backoff or continue without enrichment. |
| `UPSTREAM_UNAVAILABLE` | Transient/degradable | Retry enrichment later or continue without it. |
| `SOURCE_IDENTITY_MISMATCH` | Requires decision | Disable alignment, select the correct record, or upload the correct source. |
| `OUTPUT_VALIDATION_FAILED` | Deterministic/operator | Preserve diagnostics, do not publish, investigate engine regression. |
| `QUOTA_EXCEEDED` | Policy | Wait, delete stored data, or adjust the safety limit. |
| `INTERNAL_ERROR` | Classified server-side | Retry if marked safe; show correlation ID. |

Raw exception strings, filesystem paths, provider internals, and document content are not returned to clients.

## 11. Ingestion design

### 11.1 Upload

1. `POST /api/v1/uploads` authorizes the user, checks safety limits, and creates an expiring upload grant with a server-selected object key and maximum size.
2. The browser uploads the PDF directly to the private bucket.
3. `POST /api/v1/uploads/{id}/complete` verifies object ownership, observed size, SHA-256, basic MIME/magic bytes, and one-time grant state.
4. A source/user document is created and a validation job is queued.
5. Full PDF parsing happens only in the isolated extraction worker.

Initial configurable safeguards (per-document safety bounds; §17.6):

- source PDF maximum: 100 MiB;
- maximum pages: 2,000;
- maximum rendered dimension and total pixels per page;
- maximum total job scratch disk and wall time;
- a single global maximum-concurrent-extractions constant.

Limits are enforced again by the worker because client and edge checks are not trusted.

### 11.2 Fetch by patent identifier

- Parse a structured US identifier and classify it as a grant or application; require a supported kind or an explicit resolution result.
- Fetch only through the restricted enrichment/fetch worker.
- Allow HTTPS and approved Google Patents/patent-image hosts only; validate every redirect and final host/IP.
- Cap redirects, HTML bytes, PDF bytes, connection time, read time, and total time.
- Validate response type, magic bytes, parser readability, and resolved canonical identity.
- Distinguish not found, forbidden, rate limited, transient upstream, timeout, and malformed response.
- Store source URL, fetch time, canonical ID, and content hash; do not rely on the URL as identity.

### 11.3 Portable import

Portable imports are untrusted data and never restore server identity or authorization fields.

- Accept only documented MIME types and schema versions.
- Stream/limit JSON bytes, nesting, entries, string lengths, map sizes, and bookmarks.
- Validate the typed locator, page indices, finite normalized line/figure/callout boxes, source/display spans, enumerations, confidence ranges, figure/callout candidates, mention associations, and cross-object consistency.
- Ignore and regenerate user IDs, artifact IDs, storage keys, trust claims, and engine attestations.
- Convert legacy version-1 saves through a dedicated validator/migrator and label missing provenance.
- Render imported strings with DOM APIs, not `innerHTML` concatenation.
- The first hosted format is `.patent-viewer.json`; source PDF is supplied separately. A later archive format must defend against traversal, duplicate names, compression bombs, symlinks, and hash mismatch.
- Imported bookmark/citation-profile conflicts present explicit replace, merge, rename, or keep-both choices.
- An import without a matching verified PDF is labeled `imported_unverified` and remains text-only until reprocessed.

## 12. Page-level hybrid extraction

### 12.1 Preflight and page analysis

Each page produces a reusable analysis (part of the `source_ready` checkpoint) containing dimensions, crop/rotation, native words/text, image coverage, text density, header/gutter evidence, likely page class, and safety/quality signals. A low-resolution classification pass avoids unnecessary high-DPI OCR.

Page classes include cover, bibliographic, drawing, specification/claims, references, certificate/back matter, blank, and unknown. Classification uses page features plus document-level sequence/continuity; it does not rely on a single page in isolation.

The system identifies a plausible contiguous specification block. For grants it establishes an expected column sequence; for applications it establishes expected paragraph-number continuity. It records ambiguity instead of forcing every page into a specification.

### 12.2 Native candidate

Native extraction retains word coordinates and calculates a quality vector:

- for grants: valid consecutive column headers, gutter anchor count and horizontal consistency, robust `(y, printed_line)` fit residual;
- for applications: detected paragraph-number markers and their monotonic sequence;
- body text density and extent;
- malformed/replacement-character ratio;
- line grouping consistency;
- reference monotonicity and duplicate rate;
- continuity with neighboring pages;
- page size/rotation compatibility.

Native success is not defined as non-empty entries. Strong pages proceed natively; weak or contradictory pages are scheduled for OCR.

### 12.3 OCR candidate

- Render only selected pages at a bounded resolution appropriate to their dimensions.
- Preserve OCR block, paragraph, line, word, and confidence identifiers rather than discarding them.
- Enforce per-page timeout, total job deadline, process memory/CPU/PID/scratch limits, and cancellation.
- Record engine, language data, version, DPI, page segmentation mode, and confidence distribution.
- A readiness smoke test proves the executable and language data work; import success alone does not mean OCR is available.
- OCR failure on one page does not automatically mean the specification ended. The sequence validator uses bounded lookahead and reports unresolved gaps.

**Open decision — OCR engine.** The prototype uses Tesseract. Because comfortable in-place reading of scanned patents is a core need, OCR quality is load-bearing. Before committing, measure Tesseract on the scanned third of the corpus (§19.1). A cloud OCR API would improve quality but would require giving the extraction worker internet egress and sending page images to a third party — breaking the untrusted-PDF isolation invariant (§5.1, §17.2) and the privacy posture — so it is not adopted without an explicit, separately reviewed decision.

### 12.4 Candidate routing and selection

The page classifier chooses `native`, `ocr`, `dual`, `skip_non_content`, or `unresolved`.

- `native`: the native candidate clearly passes the absolute gate.
- `ocr`: native text is absent or clearly defective.
- `dual`: both candidates are produced and scored because evidence is ambiguous.
- `skip_non_content`: positive evidence places the page outside target content.
- `unresolved`: neither candidate passes the minimum gate.

The first implementation chooses an entire-page candidate. It does not merge native and OCR words opportunistically. Future within-page hybrid selection requires explicitly bounded non-overlapping regions, complete coverage, and stable ordering.

After page selection, cross-page validation can selectively OCR suspect native pages with missing columns/paragraphs, impossible jumps, low coverage, or malformed text. The artifact reports `native`, `ocr`, or `hybrid`.

### 12.5 Layout and reference reconstruction

Reconstruction is a strategy chosen per document type; both feed the same typed locator.

For **grants** (`col:line`):

- Use adaptive baseline tolerance derived from glyph/word height rather than one fixed value.
- Prefer OCR-native line identifiers when reliable.
- Fit gutter markers using robust regression/RANSAC and record anchor count and residual.
- Clamp plausible printed lines and require monotonic order within each column.
- Learn body/footer margins and avoid headers, footers, certificates, and marginal text.
- Borrow geometry only when the page has positive specification evidence, matching size/rotation, plausible body density, expected column sequence, and compatible neighbors; scale borrowed geometry to target dimensions; bound consecutive borrowed pages and normally require independently detected pages on both sides.

For **applications** (paragraph numbers):

- Detect bracketed paragraph markers (`[NNNN]`) and associate following text with the paragraph.
- Require monotonic paragraph sequence and record gaps.
- Assign each line a paragraph locator; line boxes remain for highlighting even though citation is paragraph-granular.

For both:

- Mark every interpolated, extrapolated, synthetic, or borrowed reference in provenance.
- Stop extraction using sequence evidence, not because a borrowed layout always returns text.

### 12.6 Figure mapping

Figure mapping makes textual references such as `FIG. 3`, `FIGS. 4–6`, and `Figure 12A` interactive. A verified link navigates to the drawing page, centers the figure region when known, and highlights the region or its figure label. It does not merely open an approximate page.

The mapper performs four steps:

1. **Detect drawing sheets.** Use sheet markers, page classification, sparse text, drawing geometry, and document sequence. Cover-page or specification mentions of `FIG.` are not drawing-page evidence.
2. **Detect figure labels.** Extract native labels first, then use sparse-text OCR and useful rotations. Preserve the label bounding box, raw OCR text, rotation, method, and score.
3. **Segment figure regions.** On pages containing multiple figures, associate labels with non-overlapping panel/ink regions where defensible. If a region is uncertain, retain the full page plus label box rather than inventing an exact panel.
4. **Parse text references.** Record source/display spans for figure mentions and expand lists/ranges such as `FIGS. 1, 2 and 3`, `1–4`, and `3A–3C`. Each expanded ID resolves independently to verified, probable, inferred, or unresolved candidates.

Additional rules:

- Restrict high-confidence labels to pages classified as drawings or independently confirmed by sheet markers.
- Retain all candidate pages, regions, labels, and evidence before choosing one.
- Never let inferred mappings serve as evidence for later inference.
- Nearest-number, base-figure, and sequential inference remain suggestions, not authoritative clickable links.
- A figure-reference mention stores stable entry ID, source/display half-open spans, expanded figure IDs, selected region candidates, status, and warnings. The viewer renders from these validated spans rather than rerunning a broad regex over untrusted text.
- Failure to map a figure leaves the text readable and explicitly unlinked; it never fails specification extraction.

### 12.7 Reference-numeral callout linking

Reference-numeral linking connects component identifiers in prose to the matching printed labels on drawings. The initial feature **locates and highlights the printed numeral itself**. It does not claim to identify the physical component boundary or trace a leader line unless a future, separately validated computer-vision stage records that evidence and provenance.

#### Drawing callout detection

For every detected figure region or drawing page:

1. Extract native text tokens and boxes where a usable drawing text layer exists.
2. Run sparse-text OCR optimized for short digit/alphanumeric labels, including bounded orientation passes. Preserve raw token, normalized value, box, rotation, engine confidence, and OCR profile.
3. Normalize supported callouts such as `104`, `104A`, `104-a`, `104′`, or parenthesized forms under a versioned grammar. Canonical values remain strings so leading zeros, suffixes, primes, and qualifiers are not lost. Do not conflate `1O4` and `104` unless OCR-confusion evidence supports the correction; otherwise retain alternatives.
4. Associate each callout with a figure region using containment, label/panel boundaries, and proximity. If region assignment is uncertain, retain page-level candidates.
5. Exclude sheet/page numbers, patent headers, dates, dimensions, coordinate axes, figure labels, and other numeric drawing text using location, context, token form, and repeated page-header evidence.
6. Merge duplicate native/OCR detections by box overlap and normalized value while retaining all provenance.

The output is a numeral index from normalized value to every callout occurrence. Detection caps per page/artifact prevent a pathological drawing or OCR result from creating unbounded overlays.

#### Text mention detection

Reference-numeral mentions are extracted from source and display text using patent-specific lexical/context rules:

- strong patterns include a noun phrase followed by a numeral, `reference numeral 104`, `element 104`, and coordinated forms such as `housings 104 and 106`;
- nearby explicit figure references and the figure-description paragraph establish figure context;
- claim numbers, paragraph numbers, years, measurements, percentages, equations, patent citations, `column:line`/paragraph references, page numbers, and the numeral inside `FIG. 104` are excluded unless stronger component-reference evidence exists;
- lists and ranges are expanded only when their grammar and the drawing-callout inventory support reference numerals rather than a numeric quantity; `110–118` does not automatically invent every integer in that interval;
- source/display offsets are recorded separately and validated against the exact token.

The detector records the nearby component label (`housing`, `shaft`, etc.) when available. That label assists association and user explanation but is not treated as authoritative ontology.

Identity-verified provider markup such as structured figure references/callouts may be retained as an untrusted supplemental detection hint. It never supplies PDF geometry and never overrides contrary source evidence.

#### Mention-to-callout association (v1: simple; ranker deferred)

**v1 association is deliberately simple:** a mention links to callouts by (1) exact normalized value match and (2) figure context established by the surrounding figure reference or figure-description paragraph. Outcomes:

- `verified`: exactly one callout of that value in the contextually indicated figure;
- `probable`: a clear default with labeled alternatives;
- `ambiguous`: two or more plausible callouts — the user chooses from a candidate chooser; there is **no silent default**;
- `unresolved`: no defensible callout; text remains unlinked.

All candidates and their evidence/scores are stored. A **calibrated multi-signal ranker** — adding co-occurrence of neighboring numerals, uniqueness within the discussed figure set, continuity with earlier mentions of the same component label, and callout/figure-confidence weighting — is a **deferred, drop-in upgrade** behind the stored `candidates` array. It requires labeled association ground truth (§19.1) to calibrate and does not change the artifact schema. Nearest-page or nearest-figure alone never produces a verified association.

If the selected figure contains several occurrences of the same callout value, the viewer highlights all of them and centers the primary/combined region. A user choice or correction is stored as `ReferenceLinkOverride`, not as a mutation of the artifact.

#### Highlighting and navigation behavior

- Figure-reference text and reference-numeral text use distinct visual styles and accessible names.
- Hover or keyboard focus may show a private thumbnail/preview with figure ID, page, callout highlight, confidence, and alternatives.
- Activating a figure reference opens the PDF pane, navigates to the page, centers the figure region, and highlights the region/label.
- Activating a reference numeral opens the PDF pane, navigates to the selected figure, zooms/scrolls to the exact normalized callout box, and applies a persistent selection highlight plus a short reduced-motion-safe emphasis.
- The corresponding inline mention remains selected so the text/drawing relationship is visible.
- Ambiguous mentions open an accessible candidate chooser with figure IDs, page numbers, thumbnails when available, and evidence labels.
- A source-side Callouts list provides an accessible non-canvas alternative and supports reverse navigation from a drawing callout to every linked text mention.
- Highlights are positioned from normalized boxes and remain correct at any PDF zoom, rotation, or responsive layout.

### 12.8 Output validation

Before publication:

- validate schema and object hashes;
- ensure all entries reference existing pages;
- validate finite/ordered boxes;
- verify entry ordering and stable IDs;
- validate locator syntax per document type, monotonicity, and documented gaps;
- verify figure page ranges and provenance;
- validate figure-reference and reference-numeral spans against their source/display strings;
- ensure callout boxes/figure regions are in bounds and reference existing pages;
- ensure every selected callout appears in the mention candidate set and has the same normalized value;
- prevent inferred/ambiguous callouts from being labeled verified or linked silently;
- calculate quality summaries from evidence;
- ensure source text exists wherever display text exists;
- ensure no low-confidence transformation is mislabeled high confidence.

An unresolved page inside the probable specification interval yields `partial`, not complete. Missing enrichment, unresolved figures, or unresolved reference numerals do not fail text extraction; their capability remains absent or qualified and is reflected in the quality report.

## 13. External enrichment and clean-text alignment

Google Patents enrichment is optional and independently retryable. Per-line clean-text alignment is **retained in full**: comfortable in-place reading of scanned patents is a core need, so the alignment that maps clean provider text onto PDF line geometry is a v1 capability. Enrichment and fetch run in the restricted-egress worker.

### 13.1 Privacy and policy

- Instance policy (operator configuration) controls whether external enrichment is enabled, disabled, or user-selectable.
- The ingestion UI discloses that the canonical patent identifier is sent to the provider. Uploaded PDF bytes and selections are not sent for alignment.
- A user may process without provider access and request enrichment later.

### 13.2 Identity verification

Before adding display text, compare the source-extracted patent number plus available application number/title/inventor evidence against the provider record. Outcomes are `verified`, `probable`, `mismatch`, or `insufficient`.

- `verified` permits configured alignment.
- `probable` may permit alignment with a warning under instance policy.
- `mismatch` disables text alignment and requires correction/confirmation.
- `insufficient` preserves metadata-only enrichment or source text unless policy explicitly allows more.

The enrichment snapshot records evidence, provider URL, fetch time, adapter/parser version, and content hashes.

### 13.3 Alignment quality

- Fetch and align description and claims as distinct sections.
- Preserve source and display text for every line.
- Score exact/fuzzy matches per line with character/token-weighted coverage, score distribution, cursor jump/backtrack penalties, and paragraph consistency.
- Reject low-confidence substitutions individually even when document-level coverage is adequate.
- Aggregate gates consider characters/tokens, sections, and confidence — not only line counts.
- Expose a source/aligned diff and allow copying either stream.
- Provider/parsing failure completes the core artifact without enrichment and with a retryable warning.
- A changed upstream snapshot creates a new enrichment/artifact revision; it never silently mutates a ready artifact.

## 14. HTTP and event API

All routes are versioned, authenticated, **per-user authorized**, rate-limited as appropriate, and return stable error envelopes with a correlation ID.

Document creation snapshots extraction choices rather than reading mutable user preferences later:

```json
{
  "source": {"type": "upload", "upload_id": "upl_..."},
  "extraction": {
    "ocr_policy": "auto",
    "language": "eng",
    "enrichment": "disabled"
  },
  "retention_policy_id": "instance_default"
}
```

`ocr_policy` supports `auto`, `force`, and `disabled`. `auto` permits page-level hybrid extraction. The job records the exact effective configuration and enrichment consent.

### 14.1 Core resources

| Method and route | Purpose |
|---|---|
| `POST /api/v1/uploads` | Create a constrained direct-upload grant. |
| `POST /api/v1/uploads/{upload_id}/complete` | Verify upload and create source/user document. |
| `POST /api/v1/documents` | Create from an upload or canonical patent identifier; return document/job. |
| `POST /api/v1/imports` | Analyze a portable import in quarantine. |
| `POST /api/v1/imports/{import_id}/commit` | Commit explicit migration/conflict choices. |
| `GET /api/v1/documents` | List the user's documents with status, warnings, retention, filters, and cursor paging. |
| `GET/PATCH/DELETE /api/v1/documents/{document_id}` | Read, update allowed metadata, or start audited deletion. |
| `POST /api/v1/documents/{document_id}/extractions` | Create a new extraction/reprocess job. |
| `GET /api/v1/documents/{document_id}/artifacts` | List the active and rollback artifact versions. |
| `GET /api/v1/artifacts/{artifact_id}` | Get manifest, maps, quality, and provenance summary. |
| `GET /api/v1/artifacts/{artifact_id}/entries` | Retrieve compressed, cursor-paged, or range-addressed entries. |
| `GET /api/v1/artifacts/{artifact_id}/figures` | Get figure regions, textual figure-reference mentions, candidates, and provenance. |
| `GET /api/v1/artifacts/{artifact_id}/callouts` | Get drawing callout occurrences and text reference-numeral mentions, filterable by figure, numeral, entry, or mention. |
| `GET /api/v1/documents/{document_id}/source.pdf` | Authorized HTTP Range-capable source delivery. |
| `POST /api/v1/documents/{document_id}/exports` | Create a versioned text-only or complete export job. |
| `GET /api/v1/exports/{export_id}` | Get export status and authorized expiring download. |
| `GET /api/v1/jobs/{job_id}` | Get authoritative lifecycle, stage, progress, attempts, warnings, and allowed actions. |
| `GET /api/v1/jobs/{job_id}/events` | Reconnectable SSE progress stream. |
| `POST /api/v1/jobs/{job_id}/cancel` | Persist idempotent cancellation request. |
| `POST /api/v1/jobs/{job_id}/retry` | Retry unchanged job configuration. |
| `POST /api/v1/jobs/{job_id}/resume` | Resume from compatible page-text checkpoints. |
| `GET/POST /api/v1/documents/{id}/bookmarks` | List/create user bookmarks. |
| `PATCH/DELETE /api/v1/bookmarks/{id}` | Update/remove a bookmark. |
| `GET/POST /api/v1/documents/{id}/annotations` | List/create annotations. |
| `PATCH/DELETE /api/v1/annotations/{id}` | Update/remove an annotation. |
| `POST/PATCH/DELETE /api/v1/documents/{id}/reference-link-overrides...` | Create or manage user-reviewed ambiguous mention-to-callout choices. |
| `GET/POST/PATCH/DELETE /api/v1/citation-profiles...` | Manage personal citation profiles (base format + options). |
| `POST /api/v1/citation-profiles/preview` | Render a plain-text preview from base format + options. |

### 14.2 API semantics

- Resource IDs are full-entropy opaque identifiers.
- User identity comes from the authenticated session, never request bodies.
- Unauthorized opaque resources normally return `404` to reduce enumeration.
- `Idempotency-Key` is required for creation/cancel/retry/resume/export/deletion initiation where repeated requests are plausible.
- `202 Accepted` is used for asynchronous creation with `Location` pointing to the resource/job.
- `ETag`/`If-Match` protect mutable resources from lost updates; `If-None-Match` supports efficient reads.
- PDFs support Range requests, safe `Content-Disposition`, and private caching; storage keys never appear in responses.
- Figure/callout endpoints return only validated numeric boxes, spans, IDs, and plain text. They support ETags and are normally loaded with the active artifact so inline links and PDF overlays use one artifact revision.
- Lists use opaque cursors and apply the owner predicate before sorting/limiting.
- All timestamps are RFC 3339 UTC.
- `429` safety-limit/rate failures include `Retry-After` when meaningful.

Standard error envelope:

```json
{
  "error": {
    "code": "PDF_PAGE_LIMIT_EXCEEDED",
    "message": "This PDF has more pages than the configured limit.",
    "request_id": "req_...",
    "retryable": false,
    "details": {"limit": 2000}
  }
}
```

Job failures additionally expose failed stage, safe remediation, checkpoint availability, and whether retry/resume is allowed. Stack traces, paths, content, and upstream response bodies stay in protected logs.

## 15. Citation system (presets and structured options)

### 15.1 Goals

- Provide useful patent-specific defaults for both grants (`col:line`) and applications (paragraph).
- Let users configure citation text and copy layout with safe structured options — **without** authoring template strings or executing code.
- Correctly represent single-line, same-column, cross-column, and single/multi-paragraph selections.
- Preserve reproducibility by versioning profiles.
- Let users choose source versus display text and preview exactly what enters the clipboard.

### 15.2 Profiles: base format plus options (no template DSL)

A citation profile is a **base format** chosen from a built-in set, plus **structured formatting options**. There is no user-authored template language: no parser, no token AST, no dual server/client renderer to keep in conformance, and therefore no template-injection surface. The user-authored template DSL is deferred (§2.2); if a real need for a format the presets cannot express appears, it can be added later behind the same profile abstraction.

A profile selects the appropriate variant automatically based on selection shape and document type (single line, same-column range, cross-column range, single paragraph, paragraph range).

```json
{
  "schema_version": 1,
  "name": "My litigation format",
  "base_format": "full_legal",
  "text_source": "display",
  "copy_mode": "quote_and_citation",
  "options": {
    "separator": "space",
    "quote_style": "curly",
    "preserve_paragraphs": true,
    "dehyphenate_from_metadata": true,
    "bracketed": false,
    "terminal_punctuation": ".",
    "dash_style": "en"
  }
}
```

The renderer is a single server-side function that produces `text/plain`, given the base format, options, selection, and document type. Validation covers: enumerated option values only; bounded profile name and rendered output; Unicode NFC normalization; rejection of NUL and bidirectional control characters; a required preview before save; immutable system defaults (users copy a default before modifying it).

Representative rendered values by document type:

| Selection | Grant | Application |
|---|---|---|
| Single reference | `col. 3, l. 15` | `¶ [0042]` |
| Same-unit range | `col. 3, ll. 15–20` | `¶¶ [0042]–[0045]` |
| Cross-unit range | `col. 3, l. 65 – col. 4, l. 4` | `¶¶ [0042]–[0051]` |

Representative label tokens available to base formats: patent number/canonical/full label/short label/title/kind, inventor primary surname, source page start/end, quality label, and access date. These are substituted by the renderer, not exposed as a user-writable template grammar.

### 15.3 Default citation profiles

| Profile | Grant example | Application example |
|---|---|---|
| Full legal | `U.S. Patent No. 12,262,260, col. 3, ll. 15–20` | `U.S. Patent Application Pub. No. 2024/0123456, ¶¶ [0042]–[0045]` |
| Compact | `US 12,262,260 B2, 3:15–20` | `US 2024/0123456 A1, ¶¶ [0042]–[0045]` |
| Short | `’260 patent, col. 3, ll. 15–20` | `’456 publication, ¶¶ [0042]–[0045]` |
| Inventor compact | `Smith, 3:15–20` | `Smith, ¶¶ [0042]–[0045]` |
| Reference only | `col. 3, ll. 15–20` | `¶¶ [0042]–[0045]` |
| Bracketed | `[US 12,262,260 B2, 3:15–20]` | `[US 2024/0123456 A1, ¶¶ [0042]–[0045]]` |
| No citation | Empty citation; selected text only. | Empty citation; selected text only. |

Users create personal profiles by choosing a base format and setting options, and select a default; documents can remember a per-user override without modifying the profile or artifact.

### 15.4 Copy layout

Citation text and clipboard layout are separate:

- `citation_only`: `{citation}`
- `quote_and_citation`: `“{quote}” {citation}.`
- `quote_newline_citation`: `{quote}\n{citation}`
- `text_only`: `{quote}`

`{quote}` is plain text from `display_text` or `source_text`; it is never interpreted as markup. Options control quote style, separator, paragraph preservation, metadata-driven dehyphenation, terminal punctuation, dash style, and preferred text source. The copy preview exactly matches the clipboard.

Smart-copy interception is opt-in/configurable. The default interaction uses a selection popover with separate Copy text, Copy citation, and Copy text with citation commands so ordinary browser copy remains predictable.

### 15.5 Confidence behavior

- The copy panel shows text/reference confidence for the selected range.
- Low-confidence, synthetic, or inferred references display Verify in source and a direct PDF action.
- Instance policy may require confirmation when a selection crosses unresolved pages/references.
- Citation output never implies continuity across an unresolved page.
- Export metadata records profile ID/version and artifact ID for reproducibility.

## 16. Hosted user experience

### 16.1 Documents view

Replace separate recent/server/saved lists with one Documents experience showing:

- title and canonical patent identity (grant or application);
- source type and owner;
- document state, current job stage, or artifact disposition;
- native/OCR/hybrid mode;
- quality warnings;
- last opened and created times;
- expiry and retained size;
- active artifact/engine version;
- open, resume, cancel, retry, reprocess, export, and delete actions.

Derived states include Preparing source, Processing, Ready, Ready with warnings, Reprocessing, Action required, Failed, Text only, and Deleting. An active artifact remains readable while a new job runs or fails.

### 16.2 Ingestion experience

- Offer Upload PDF, Fetch Patent, and Import Portable Save as distinct choices.
- Explain retention, limits, and external enrichment before submission.
- Validate patent format (grant/application) and upload constraints immediately without trusting client validation.
- Prevent duplicate submissions through idempotency and one client state machine.
- Stay in the same tab; do not depend on delayed popups.

### 16.3 Real progress, cancellation, retry, and resumability

The job UI mirrors authoritative stages: validating, fetching, classifying, native extraction, OCR, printed references, figure regions, reference-numeral callouts/links, enrichment, alignment, quality, and persistence. It shows real page/work-unit counts, warning/recovery actions, and no fabricated timer.

Users may navigate away. Returning reloads the job snapshot and reconnects SSE from the known sequence. Cancel/retry/resume buttons reflect server-confirmed capability. Failures offer specific recovery such as Try OCR, Continue without enrichment, correct patent identity, resume from checkpoints, or contact support with a correlation ID.

### 16.4 Viewer layout and source tools

Desktop uses resizable outline, specification, and PDF regions. Tablet supports split or stacked views. Narrow screens use accessible Text, Source, and Details tabs while retaining the current reference/search position.

PDF controls include page entry, previous/next, zoom, fit width/page, rotation, and authorized download/open-original. The overlay layer supports line boxes, figure-region boxes, figure-label boxes, and exact reference-numeral callout boxes. Absent boxes produce a clear page-only or unavailable message.

Bookmark, Annotate, and View source are distinct controls. Clicking a line selects it and can update the deep link but does not prompt unexpectedly. Raw/display comparison highlights changed tokens and explains alignment provenance. Inferred figures show candidates and confidence.

Within specification text:

- a figure reference such as `FIG. 12A` is highlighted as a figure link and announces its mapped figure/page and confidence;
- a linkable component numeral such as `104` is highlighted separately and announces its component label when known, target figure, page, and confidence;
- hover/focus provides a source preview when policy/performance permits;
- activation keeps the text mention selected while the PDF opens, centers the relevant region, and highlights the figure or exact callout;
- ambiguous numerals open a candidate chooser instead of navigating arbitrarily;
- unresolved numerals remain ordinary readable text or show a non-interactive uncertainty marker according to user preference;
- users may independently toggle figure links, reference-numeral links, and tentative suggestions; visual annotations and accessible labels never alter copied patent text or citations;
- when the same callout occurs several times, the source pane exposes Previous occurrence, Next occurrence, and Return to text actions.

### 16.5 Navigation and search

- Generated outlines identify themselves as generated and show uncertainty/missing sections.
- Search scopes include all text, specification, claims, description, headings, raw text, display text, bookmarks, and annotations.
- Results show snippets/counts and support keyboard next/previous.
- Jump accepts tolerant human references (grant `col:line` or application paragraph) but resolves to stable entry IDs.
- Authorized URLs deep-link to artifact entry, figure, reference-numeral mention/callout, PDF page, or search state without exposing storage keys. A callout deep link includes the artifact revision so reprocessing cannot silently point it at changed evidence.
- Missing source mappings provide explicit feedback rather than doing nothing.

### 16.6 Bookmarks, annotations, correction, and artifacts

- Bookmarks/annotations are server-side per user and target stable artifact entry IDs.
- On reprocessing, targets re-anchor by exact locator + text; unmatched targets are flagged "review / re-place." There is no silent rewrite, no fuzzy migration service, and no side-by-side comparison UI in v1.
- Users can report incorrect text, printed reference, outline, claim, figure mapping, callout detection, or mention-to-callout association with artifact/entry/mention/callout IDs and optional consented evidence.
- Selecting a callout in the PDF overlay can reveal every linked mention in the specification, providing reverse navigation as an extension of the same immutable mapping.

### 16.7 Export and portability

- Text-only export contains schema, source identity/hash, source/display entries with typed locators, figure regions/references, callout occurrences, reference-numeral mentions/candidates, provenance, quality, selected bookmarks/annotations/overrides, and citation profile snapshot/reference.
- Complete export may additionally include the source PDF under a larger explicit download policy.
- Exports are immutable, private, expiring objects with hashes and declared optional sections.
- Imports analyze first, show conflicts/warnings, and commit only explicit choices.

### 16.8 Accessibility

Accessible **by construction** from day one, with a formal audit right-sized to actual assistive-technology users:

- semantic landmarks, buttons, links, headings, lists, dialogs, and form labels;
- keyboard-complete job, outline, search, source, citation, bookmark, and annotation flows;
- visible focus and predictable focus movement after navigation;
- `aria-expanded`, `aria-current`, and live progress/search/copy/warning announcements;
- non-color-only confidence and synchronized states;
- keyboard-focusable inline figure/reference-numeral links and an accessible Callouts list mirroring canvas overlays;
- contrast-compliant themes and configurable type size/line height;
- 400% zoom/reflow, reduced motion, high contrast, and usable touch targets;
- PDF text layer or clearly identified accessible source-text alternative.

A formal multi-screen-reader conformance audit (e.g. NVDA + VoiceOver) is scoped to the assistive technologies that actual users depend on; when a user needs one, the build is tested against what they use. Accessible-by-construction is unconditional; the drilled audit is scaled to real need.

## 17. Security and privacy

### 17.1 Authentication and authorization

- Use OIDC/OAuth 2.1 with short-lived server sessions in Secure, HttpOnly, SameSite cookies; do not store bearer tokens in browser local storage.
- Validate CSRF tokens and Origin/Host for mutations.
- Resolve the user server-side. Every resource query includes an **owner predicate** before results are revealed.
- Use full-entropy opaque IDs; authorization remains mandatory even when IDs are unguessable.
- Operator/support access requires a separate role, MFA, reason, and an audit event.
- Documents are private to their owner by default. Anonymous/public links and inter-user sharing are out of scope for the first release.

### 17.2 Untrusted PDF isolation

- Web/API processes never parse PDFs.
- The extraction worker runs rootless with a read-only image, private per-job scratch, no platform secrets, no shared filesystem, and **no network**.
- Enforce CPU, memory, PID, temp-disk, page, pixel, derived-output, and wall-clock ceilings.
- Launch PDFium/Tesseract in supervised process groups so cancellation or timeout kills the full tree.
- Reject encrypted PDFs in the first release rather than persisting passwords.
- Treat parser crashes as job failures, capture content-free diagnostics, and recycle the worker sandbox.

### 17.3 Web and rendering security

- Treat all extracted, OCR, provider, imported, bookmark, annotation, and citation strings as untrusted.
- Render with DOM node properties and `textContent`; never concatenate untrusted values into `innerHTML`, attributes, style, or selectors.
- Validate and normalize values before setting `dataset` or numeric style properties.
- Validate text-span offsets, figure IDs, numeral values, rotations, candidate relationships, and normalized overlay boxes before creating inline links or PDF highlights. Overlay coordinates set numeric style properties only after finite/range checks.
- Serve a restrictive CSP without `unsafe-inline`; use nonced/static scripts and consider Trusted Types.
- Apply HSTS, `X-Content-Type-Options`, restrictive frame/referrer policies, and an appropriate permissions policy.
- Maintain and update bundled PDF.js through a documented dependency process.
- Return JSON with correct content type and avoid embedding artifact JSON into executable script contexts.

### 17.4 External network controls

- Only the enrichment/fetch worker has outbound access.
- Allow HTTPS and explicit hostnames; validate DNS/IP and host after every redirect to prevent SSRF and rebinding.
- Apply connection/read/total timeouts, byte ceilings, redirect caps, response-type validation, bounded retries, backoff, and circuit breaking.
- Never pass a user-supplied arbitrary URL to the worker.
- Fetched blobs enter the same isolated validation pipeline as uploads.

### 17.5 Data protection

- TLS in transit and provider-managed encryption at rest.
- Private buckets with public access disabled.
- Authorized Range-capable PDF delivery or very short-lived scoped delivery tokens; sensitive URLs are excluded from logs and referrers.
- Instance-selectable external-enrichment policy and user-visible disclosure.
- Do not log PDF text, selections, annotations, citation text, imported content, or sensitive source URLs.
- Redact filenames/patent identifiers from general metrics where policy requires it.
- Support retention/deletion policy and auditable completion.

### 17.6 Resource-safety limits

The system enforces **document-safety bounds** (defenses against malicious or pathological input, independent of user count) plus **one global concurrency constant**. It does **not** implement per-tenant fair scheduling, admission control, or daily budgets — there is no multi-tenant contention to arbitrate among 30 known users.

Safety bounds (enforced and surfaced):

- upload and remote-download bytes;
- pages and raster pixels;
- per-page and total-job wall time and scratch disk;
- maximum figure regions, callout occurrences, mention candidates, and overlay elements per page/artifact;
- citation-profile/import sizes;
- API and SSE connection rates.

A single global "maximum concurrent extractions" constant prevents a burst from overwhelming the worker box. It is configuration, not a scheduling subsystem.

### 17.7 Supply chain

- Pin Python dependencies and base image digests; lock hashes where supported.
- Pin system packages through reproducible images or signed snapshots.
- Generate an SBOM, scan dependencies/images, and maintain an update policy for PDFium, Tesseract, Pillow, pdfplumber, Flask, and PDF.js.
- Run minimal non-root images and apply least-privilege service identities.
- Canary extraction-engine changes under a new engine version and compare golden outputs before broad rollout.

## 18. Reliability and operations

### 18.1 Health and readiness

- `/live` reports process liveness only.
- `/ready` verifies required database and object-store access for the role.
- Worker image readiness includes a real small PDFium/Tesseract smoke test and language-data check.
- Google availability is not a web-service readiness dependency because enrichment degrades independently.

### 18.2 Backpressure and scaling

- The extraction worker enforces a hard concurrency cap; the global concurrency constant bounds total in-flight work.
- Native and OCR are internal modules of one worker; they are split into separate pools only when measured load requires it.
- When saturated, the API stays responsive, reports honest queue state, and rejects above the global concurrency limit rather than accepting unbounded work.
- Autoscaling is not required at this scale; scale is adjusted manually if needed.

### 18.3 Observability

Metrics:

- request rate/latency/error by stable code;
- queue depth (ready job rows) and oldest age;
- stage/page duration and throughput;
- native/OCR/hybrid proportions;
- fallback and unresolved-page rate;
- alignment, figure-region, callout-detection, mention-association, and printed-reference confidence distributions;
- lease expiry, retry, resume, cancellation, and worker-loss rates;
- CPU, peak RSS, scratch usage, and rendered pixels;
- object/database errors, staging/orphan cleanup, retained bytes;
- provider latency, 429/4xx/5xx, and parser success;
- deletion lag and audit completion.

Traces link request, job, attempt, stage, and object operations using IDs without content payloads. Logs are structured, redacted, access-controlled, and use correlation IDs. Audit logs cover authorization failures, exports, policy changes, operator actions, and deletion.

### 18.4 Data durability and recovery

- Managed PostgreSQL uses **point-in-time recovery (PITR)** and provider-managed backups. This is the primary durability guarantee and is retained unconditionally.
- Object storage uses provider durability, lifecycle, and versioning consistent with deletion commitments.
- The job queue is part of PostgreSQL and recovers with it; there is no separate broker state to reconcile.
- Backups are restorable. Rehearsed disaster-recovery drills and formal RPO/RTO targets are **not** launch gates at this scale; a documented restore procedure is sufficient.
- Schema migrations run as a dedicated compatible release step with a rollback/roll-forward procedure, not opportunistically by every process.

### 18.5 Graceful degradation

- Google outage: artifact completes without enrichment and can enrich later.
- Figure mapping failure: text artifact completes; uncertain/missing links are hidden or qualified.
- PDF delivery failure: text remains readable and the UI reports source unavailability.
- Worker loss: lease expires and work resumes from verified page-text checkpoints.
- SSE loss: client polls the snapshot and reconnects with last event ID.
- New extraction failure: prior ready artifact remains active.
- Storage/database outage: job does not claim success; retries follow classified policy.
- Unresolved page inside the probable specification: artifact remains partial, and citation output does not imply continuity across the gap.

## 19. Verification and test strategy

### 19.1 Extraction benchmark corpus

Begin with 20–30 labeled documents before moving algorithms, then grow toward at least 150 public patents for launch, split across born-digital, scanned, and hybrid, and across **grants and application publications**. Reserve a locked holdout set. Hybrid fixtures must include good early text layers followed by missing or corrupt later layers.

Stratify by:

- document type (grant vs application) and era/layout variation;
- native, scanned, and hybrid documents;
- missing/irregular gutter markers (grants) and irregular paragraph numbering (applications);
- one-column final pages and sparse claims;
- rotated and multi-figure drawing sheets;
- formulas, tables, symbols, hyphenation, and OCR-confusable glyphs;
- certificates/back matter after the specification;
- malformed, encrypted, oversized, corrupt-mid-document, and non-patent PDFs.

Annotate page roles/specification boundaries, representative references/boxes (per document type), figure labels/regions, drawing callout boxes, text reference-numeral spans, and correct mention-to-callout associations, including deliberately ambiguous/unresolved cases (needed to calibrate the deferred association ranker, §12.7). Measure:

- specification-page precision/recall;
- OCR-routing recall;
- text character and word error rates;
- printed-reference (grant) and paragraph-number (application) exact and ±1 accuracy;
- bounding-box intersection-over-union;
- high-confidence coverage and precision separately;
- incorrect external-text substitution rate;
- figure-page precision/coverage by provenance class;
- figure-region box accuracy, callout detection precision/recall, text-mention precision/recall, and (for the simple v1 associator and later ranker) mention-to-callout association precision/coverage by confidence class;
- p50/p95 duration, CPU, peak RSS, scratch, and checkpoint reuse.

Golden comparisons operate at document, page, and line levels with coordinate tolerances. Engine changes produce reviewed diffs and a new recorded engine/config version.

### 19.2 Provisional extraction release targets

Targets are recalibrated only through an explicit decision after corpus evidence. Because extraction quality is validated in Phase 3 (infra-first order), these gates are the primary technical risk and should be checked against a mini-corpus as early as feasible.

| Measure | Provisional gate |
|---|---|
| Specification-page classification | At least 99.5% recall and 99.0% precision; no silent omission in the fixed golden set. |
| Pages requiring OCR | At least 99% routing recall. |
| Printed/paragraph reference | At least 98.5% exact and 99.5% within one unit. |
| High-confidence references | At least 99.5% empirically correct. |
| External aligned substitutions | At least 99.5% precision and 100% rejection of known wrong-document fixtures. |
| Direct figure mapping | At least 99% precision and no incorrect `verified` mapping in the golden set. |
| Figure reference region | At least 99% precision for links labeled verified; every verified link has an in-bounds page/region or label box. |
| Drawing callout detection | At least 98% precision and 95% recall on supported drawings; no header/sheet/page number labeled as a verified callout in the golden set. |
| Text reference-numeral detection | At least 98% precision; numeric quantities/claims/patent citations remain below the agreed false-link rate. |
| Verified mention-to-callout association (v1 simple) | High precision on the confident subset; ambiguous fixtures never receive a silent verified destination. The calibrated ranker later targets ≥99.5% precision. |
| Entry geometry | 100% boxes in bounds; median line-box IoU at least 0.85. |
| Determinism | Semantically equivalent ordered output for repeated source/config runs, excluding IDs/timestamps. |
| Resume | No repetition of valid page checkpoints; same semantic result as an uninterrupted run. |
| Fault injection | Worker loss, duplicate claim, storage failure, and adapter timeout never publish a partial artifact as complete. |
| Containment | Every adversarial fixture terminates within configured resource limits without affecting the web tier. |

Performance budgets are set on a named reference worker. After a baseline, releases that regress throughput or peak memory materially require an approved exception.

### 19.3 Unit and property tests

- structured patent identifier parsing (grant and application);
- page classification and sequence boundaries;
- grant gutter candidates/robust fit/interpolation/monotonic references and application paragraph-marker detection;
- adaptive line grouping and coordinate normalization;
- figure ranges, rotations, region segmentation, candidates, and inference rules;
- drawing callout OCR/native deduplication, numeral normalization, header/dimension exclusion, and figure-region assignment;
- text reference-numeral grammar, exclusion rules, source/display spans, simple contextual association, ambiguity, and user overrides;
- clean-text alignment, identity gates, split words, paragraph boundaries, and selective rejection;
- quality-policy derivation;
- artifact/import/citation schemas and migrations;
- citation rendering for single, same-column, cross-column, single-paragraph, paragraph-range, missing/inferred references;
- idempotency, leases, fencing, cancellation races, retry, checkpoint compatibility, and atomic publication;
- retention/reference counting and deletion idempotency.

Property/fuzz tests cover malformed JSON, deep nesting, unexpected types, huge strings, non-finite numbers, hostile locator/figure/callout values, invalid spans/candidate graphs, Unicode controls, and selector/HTML boundary cases.

### 19.4 Integration tests

- direct upload finalization and tampered grants;
- remote redirects, host changes, oversized streams, timeout, 403/404/429/5xx, and identity mismatch;
- worker death before/after checkpoint and during publication;
- duplicate claim and stale fencing token;
- cancellation during native parsing, OCR, enrichment, and publication;
- retry/resume compatibility and reprocess immutability;
- database/object outages and recovery;
- authorization/IDOR across every resource type (per-user);
- retention, deletion, orphan cleanup, export expiry, and restore.

### 19.5 Browser and accessibility tests

- stage transitions, SSE reconnect, polling fallback, cancel/retry/resume across reload/login;
- source/display comparison, inline figure/reference-numeral links, callout overlays, ambiguity chooser, reverse navigation, and quality warnings;
- citation profile creation (base format + options), preview parity, defaults, copy modes, and low-confidence confirmation;
- selection across one line, multiple lines, columns, paragraphs, split words, and unresolved gaps;
- search scopes, outline, claims, figures, deep links, bookmarks, and annotations;
- PDF zoom/page/rotate/sync, figure-region/callout highlight alignment at multiple scales/rotations, and missing-box fallback;
- responsive desktop/tablet/mobile layouts;
- keyboard-only and accessible-by-construction checks; automated accessibility checks; targeted screen-reader testing scoped to actual AT users;
- safe rendering of hostile imported/provider/extracted strings.

### 19.6 Security and resilience tests

- per-user authorization matrix and threat-model review;
- SAST/dependency/container scans and SBOM verification;
- parser/OCR resource exhaustion and process-tree termination;
- import/DOM XSS, CSP/Trusted Types, CSRF, session fixation, and rate limits;
- SSRF, redirect, and DNS rebinding tests for the enrichment/fetch worker;
- chaos tests for worker termination, duplicate claim, lease expiry, and object latency;
- backup/restore and deletion-audit drills.

### 19.7 Release gates

- No known cross-user authorization failure.
- No unvalidated imported/provider/extracted value reaches an executable DOM context.
- No partial artifact becomes ready during fault-injection tests.
- Worker restart and duplicate claim do not duplicate/corrupt the active artifact.
- Cancellation terminates child processes within the stated bound under test.
- High-confidence outputs meet corpus precision targets; lower-confidence coverage is visibly labeled.
- No identity-mismatched provider text is silently aligned.
- Core journeys are keyboard-complete and accessible by construction; AT-specific testing covers real users' tools.
- Restore and deletion workflows pass in a production-like environment.

## 20. Deployment topology and configuration

Reference roles (minimal single-provider footprint):

- provider-managed edge/load balancer;
- one or more stateless API replicas;
- one extraction worker (native + OCR + coordinator; no internet);
- one restricted-egress enrichment/fetch worker;
- in-process (or small sidecar) lease reaper and retention/orphan janitor;
- managed PostgreSQL with PITR (system of record **and** job queue);
- managed private object storage;
- optional disposable cache/pub-sub for SSE fan-out.

There is no managed message broker, no dispatcher/outbox processor, and no autoscaling worker pools. Workers use read-only images, non-root users, private ephemeral scratch, network policy (extraction worker: no egress; enrichment worker: allowlisted egress), and explicit resource limits. Configuration is versioned wherever it affects artifacts or quality.

**Open decision — provider.** The concrete platform (e.g. managed Postgres + S3 + container host on a single cloud, or a single VPS with Docker Compose plus managed Postgres) is chosen for what the operator is comfortable running. The topology above is provider-neutral.

Secrets come from a managed secret store and are scoped by role. Database migrations are not run opportunistically by every replica. Production and staging use separate data/security boundaries.

The extraction cache key includes source SHA-256, artifact schema, pipeline/classifier/layout configuration, PDF parser/PDFium versions, OCR engine/version, OCR language/trained-data hashes, and resource profile. Alignment additionally keys on core artifact, enrichment snapshot, and alignment-engine/config versions.

## 21. Delivery plan

Phases are worked in order (infra-first, by decision). A **Phase −1** (§25) settles the stack and foundational contracts before any feature code. The prototype is offline for the duration; users wait.

### Phase 0 — safety gate and corpus

- Build the initial labeled corpus (grants and applications; born-digital/scanned/hybrid) and capture ground-truth goldens from it (§19.1). Because extraction is clean-room (§25.1), goldens come from the labeled corpus, not from prior output. This corpus is the earliest chance to sanity-check the §19.2 gates.
- Establish the safe-rendering and import-validation invariants and the structured patent parser (grant + application) as foundations of the new codebase.
- Add input/download/resource safety limits, stable errors, and real OCR readiness tests.
- Take the shared-history prototype offline.

### Phase 1 — hosted identity and durable data

- Add OIDC, users, opaque IDs, per-user authorization tests, and audit basics. No tenant/membership entities.
- Add PostgreSQL and private object storage.
- Implement `SourceDocument`, `UserDocument`, immutable artifact (active + rollback), bookmarks/annotations with simple re-anchor, and explicit retention/deletion.
- Build the validated version-1 importer into schema version 2 (typed locator).
- Deliver the unified Documents view using durable state.

### Phase 2 — isolated asynchronous execution

- Stand up the queue-driven rootless extraction worker (Postgres-backed queue via `SKIP LOCKED`) around an **initial clean-room extraction core** (grant `col:line`, native + OCR). Because extraction is net-new (§25.1), the core is built here rather than lifted from the prototype; the full hybrid / applications / callout pipeline lands in Phase 3, validated against corpus goldens.
- Add leases/fencing, attempts, durable events, cancellation, retry, resume (two-checkpoint model), and atomic publication.
- Add same-tab real-stage progress UI with SSE and polling recovery.
- Establish safety limits, the global concurrency constant, and content-free observability.

### Phase 3 — hybrid extraction and provenance

- Add page analysis/checkpoints and selective OCR (measure and decide the OCR engine here).
- Add continuity validation, robust grant gutter fitting and application paragraph detection, conservative layout borrowing, figure-region mapping, drawing callout detection, text reference-numeral extraction, simple contextual association with the ambiguity chooser, and provenance rules.
- Preserve source/display text and add line/reference/alignment confidence.
- Add identity-verified alignment, `complete_with_warnings`/`partial`, source/display comparison, and quality UI.

### Phase 4 — citation and viewer completion

- Add built-in citation profiles (grant + application), structured formatting options, defaults, previews, and copy layouts; version profiles. (The user-authored template DSL is deferred.)
- Deliver responsive/resizable viewer, richer PDF controls, inline figure/reference-numeral links, exact figure/callout overlays, ambiguity chooser, occurrence cycling/reverse navigation, scoped search, deep links, distinct source/bookmark actions, server bookmarks/annotations/overrides, reporting, and portable exports.
- Complete accessible-by-construction and supported browser/device testing.

### Phase 5 — production readiness and launch

- Complete security, dependency/image scanning, backup/restore, deletion audits, and a documented restore procedure.
- Set final safety limits, quality thresholds, retention defaults, and runbooks from benchmark evidence.
- Canary engine versions and launch to the known user group.
- (Deferred backlog, added only when a real need appears: calibrated association ranker, fuzzy bookmark migration + comparison UI, citation template DSL, multi-tenant entities, separate native/OCR pools and autoscaling.)

Launch requires identity/ownership, durable private storage, safe rendering/imports, isolated bounded work, asynchronous recovery semantics, retention/deletion, and quality provenance. These are launch requirements, not post-launch hardening.

## 22. Acceptance criteria

### Hosted data and authorization

- Every document-related endpoint proves resource ownership by the authenticated user.
- No client supplies or learns authoritative storage keys.
- A successful upload/fetch creates a durable source and recoverable job before acceptance returns.
- Worker/API restart cannot lose an acknowledged job.
- Only validated ready artifacts are viewable as complete.
- Deletion revokes access immediately and exposes completion state.

### Jobs

- Progress comes from durable stages/work units and survives reload/reconnect.
- Cancel, retry, resume, and reprocess have distinct documented semantics.
- Duplicate claim and stale workers cannot publish twice or overwrite current state.
- Resume reuses only hash/version/config-compatible page-text checkpoints.
- Prior ready artifacts remain usable when reprocessing fails.

### Extraction trust

- Every entry includes page, source/display text, typed locator, box when available, provenance, confidence, and warnings.
- Low-confidence/synthetic/borrowed/inferred results cannot appear exact.
- Native/OCR selection is page-level and cross-document continuity is validated.
- Provider identity mismatch blocks silent text alignment.
- Users can inspect and copy source text.
- Textual figure references link to validated drawing pages/regions and highlight the selected figure or label.
- Reference-numeral mentions link to matching callout boxes with a candidate chooser for ambiguous cases; activating a confident link centers and highlights the exact callout.
- Ambiguous numerals present candidate figures/callouts, unresolved numerals never navigate silently, and user choices remain separate overrides.
- Callout overlays remain aligned across PDF zoom, rotation, and responsive layouts and have a keyboard/screen-reader-accessible equivalent.

### Citation

- Users select system defaults or safe personal profiles (base format + options).
- Profiles render plain text only; there is no user-authored template language.
- Single-line, same-column, cross-column, single-paragraph, and paragraph-range variants render correctly.
- Copy preview exactly matches clipboard text.
- Profile versions are reproducible in exports.
- Low-confidence references show verification guidance and never imply continuity across a gap.

### UX and accessibility

- Documents provide one durable place to open, resume, retry, reprocess, export, and delete.
- Processing shows real stages/page progress without a fabricated timer or popup dependency.
- Source, bookmark, annotation, and copy interactions are distinct.
- Desktop, tablet, and mobile layouts remain usable.
- Core journeys are keyboard-complete and accessible by construction; AT-specific testing covers real users' tools.

### Security and operations

- Untrusted PDFs execute only in the bounded isolated extraction worker with no network.
- Imported/extracted/provider strings cannot create executable DOM content.
- Outbound fetches (enrichment worker only) are host/redirect/byte/time restricted.
- Health/readiness, metrics, traces, stable errors, audit, safety limits, backup/restore, and cleanup operate before launch.
- Logs and metrics do not contain document text, selections, annotations, or citation output.

## 23. Decisions to close before implementation freeze

Closed in this revision: document kinds (grants + applications), tenancy model (single-tenant, per-user auth), queue (Postgres-backed), worker topology (two roles), checkpoint model (two types), artifact versioning (immutable + rollback + simple re-anchor), citation approach (presets + options, DSL deferred), enrichment (full alignment retained), hosting posture (minimal footprint, PITR, best-effort availability), accessibility (by construction, right-sized audit), callout association (simple + chooser, ranker deferred), limits (safety caps + global concurrency), **extraction approach** (clean-room deterministic rewrite, §25.1), and **technology stack + hosting provider** (Python / FastAPI / React+TS / SQLAlchemy+Alembic / Supabase, §25.2).

Still open:

1. Supabase **plan and region**, and whether object storage is Supabase Storage or an external S3-compatible bucket (the provider is otherwise settled as Supabase, §25.2).
2. **OCR engine** — Tesseract vs an alternative — decided after measuring on the scanned corpus; a cloud OCR API is disallowed without a separate privacy/isolation review.
3. Default/max retention, deletion window, object-version policy, and backup-expiry language.
4. Initial upload/page/pixel/time/CPU safety-limit values and the global concurrency constant.
5. Whether Google enrichment defaults on, off, or per-document (instance policy).
6. Final high-confidence extraction, printed/paragraph-reference, figure-region, callout-detection, and association thresholds from the labeled corpus.
7. Supported browsers/devices and final UI performance budgets.

## 24. Summary

X-Ray Spec is not a web wrapper around a synchronous extractor. It is a private, per-user document system with durable ownership, isolated resumable computation, immutable versioned artifacts, and an explicit trust model for every citation-bearing line — sized for a solo operator and ~30 known users rather than a multi-tenant SaaS.

The extraction *paradigm* — a shared native/OCR coordinate abstraction and strict preservation of PDF geometry — remains the valuable idea, but it is re-implemented clean-room (§25.1), not carried over from the prototype. This design places the new engine behind golden tests seeded from a labeled corpus, page-level quality decisions, durable checkpoints, strict publication validation, and visible provenance. It generalizes the citation spine to cover both granted patents (`col:line`) and application publications (paragraphs) through a single typed locator, and it replaces process-local sessions and reduced history with private durable entities and real reconnectable stages.

It deliberately does **not** build the machinery a larger service would need but this one does not: no message broker, no outbox/dispatcher, no multi-tenant isolation or fair scheduling, no autoscaling pools, no user-authored template language, no fuzzy migration service — each retained as a backlog item behind a clean seam, addable when a real need appears. The result gives users configurable plain-text citations, richer source inspection, figure-reference navigation, component-reference links that highlight exact drawing callouts, server-backed documents and annotations, responsive accessible workflows, preserved source text, and inspectable transformations — without paying, on day one, for scale that is not there.

## 25. Rebuild prerequisites and technology stack (Phase −1)

Settled in review. A clean-room rebuild starts here, before Phase 0 code.

### 25.1 Extraction is clean-room, not reused

The prior `app.py` is discarded. Extraction is re-implemented from scratch in the same **deterministic, geometry-anchored paradigm** (§12): extract text and coordinates, detect printed gutter line-numbers (grants) or bracketed paragraph markers (applications), fit and anchor every line to a bounding box, preserve source text, and attach honest provenance.

The paradigm is deliberately **not** model-based/VLM extraction. Generative models are structurally unreliable at the product's core guarantees — exact printed line numbers, exact bounding boxes, and never confidently rewriting unreadable text — so they are not used to produce citation-bearing text or geometry. Callout and figure detection also stay deterministic (sparse-text OCR + geometry). The governing rule: **models may propose, geometry must confirm, and source text is never overwritten by a model.** Where a model is used later as a *suggestion* (e.g. an association hint), its output is validated against source geometry and confidence-gated, never treated as authoritative.

Consequence: extraction quality is **100% net-new** and, per the retained infra-first order, validated in Phase 3. This is the project's primary technical risk (see §19.2); the labeled corpus (§19.1) should be used to sanity-check the gates as early as possible.

### 25.2 Technology stack

- **Language:** Python (strongest PDF/OCR/geometry ecosystem — pypdfium2, pdfplumber, Tesseract bindings — and the most reliable for model-assisted coding).
- **Backend:** FastAPI — async (clean SSE streaming and concurrent job/DB work), Pydantic schema validation on every untrusted boundary, automatic OpenAPI.
- **Frontend:** React + TypeScript single-page app over the JSON API. **TypeScript types are generated from FastAPI's OpenAPI schema**, giving an end-to-end typed contract (server Pydantic ↔ client TS) so the client cannot silently drift off the API shape.
- **Data access:** SQLAlchemy 2.0 (async, asyncpg) + Alembic migrations. Drop to core SQL for the `SELECT … FOR UPDATE SKIP LOCKED` queue claim and owner-predicate composition. ORM models are kept **separate** from wire-layer Pydantic types (storage shape ≠ wire shape).
- **Identity + managed services:** **Supabase** — Supabase Auth (magic-link email, optionally Google) as the OIDC login, plus Supabase managed **Postgres** (PITR on a paid plan) and Supabase **object storage**. This closes the hosting-provider question (§23 #1).
  - **Integration rule:** use Supabase as *managed Postgres + Auth + Storage with the FastAPI service as the only database client* — verify Supabase-issued JWTs and connect to Postgres by connection string. **Do not** adopt Supabase's client-direct / Row-Level-Security / PostgREST access pattern; it conflicts with this design's rule that the API is the sole authority and every query carries a server-side owner predicate (§17.1).
- **PDF rendering:** PDF.js in the client (already vendored under `static/pdfjs`), maintained through the documented dependency process (§17.3).
- **OCR engine:** still open (§23 #2). Tesseract or an alternative, decided after measuring on the scanned corpus; a cloud OCR API is disallowed without a separate privacy/isolation review because it would break the no-internet extraction worker (§5.1, §17.2).

### 25.3 Foundational contracts to define before feature code

1. **Extraction-core interface** — a pure `extract(pdf_bytes, config) -> artifact` with no web/IO/global-state coupling, runnable inside the worker and unit/golden-testable in isolation.
2. **A single versioned `config` object** holding every threshold, tolerance, and DPI. It is the extraction cache key and reproducibility anchor (§20 `config_hash`); thresholds must not live as scattered magic numbers.
3. **Golden test harness** seeded from the hand-labeled corpus (§19.1), not from prior output. Document-, page-, and line-level comparison with coordinate tolerances.
4. **Repo/module structure and typed seams** — extraction core, artifact schema, API, worker, viewer — with explicit interface contracts between them, so model-assisted coding has stable boundaries and the rebuild does not slide. (A `codebase-design` task.)
5. **Overlay coordinate contract** — the normalized-box ↔ PDF.js viewport mapping across scale and rotation, with a test, so line/callout highlights stay exact at any zoom or rotation. This is historically the buggiest surface; specify it once.
6. **Test framework and CI gate** — pytest (backend) + Vitest/Playwright (frontend) with a CI gate. This matters more than usual because the model writes the tests; the gate is what keeps them honest.
