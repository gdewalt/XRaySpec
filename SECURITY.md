# Security Posture

A summary of the threat model, the controls in place, and how to report a
vulnerability. Design rationale is in [DESIGN.md §17, §19.6–19.7](DESIGN.md);
operational procedures are in [RUNBOOK.md](RUNBOOK.md).

This is a **single-tenant, per-user** application gated to an email allowlist. It
ingests **untrusted PDFs** (uploaded and fetched) and **untrusted portable
imports**, which is the dominant source of risk.

## Trust boundaries

| Boundary | Rule |
| --- | --- |
| Client → API | Identity comes only from a verified Supabase JWT; never from a request body. Email must be on the instance allowlist. |
| API → data | Every document-scoped query applies an **owner predicate**; a foreign resource returns `404`, never `403` (no existence oracle). |
| Web tier → PDFs | The web tier **never parses PDFs or runs OCR**. It only observes size / SHA-256 / magic bytes on a finalized upload. |
| Extraction worker | Runs the untrusted-PDF parsing and OCR with **no network egress**, rootless, resource-limited, in ephemeral scratch. |
| Fetch worker | **Allowlisted egress only**, behind the SSRF/rebinding guard. |
| Import | Bounded, typed validator in quarantine; all IDs/keys regenerated; text-only, never executed. |

## Controls in place

- **AuthZ:** owner-predicate on every query; cross-user IDOR matrix tested
  (`backend/tests/test_documents_authz.py`).
- **SSRF / DNS-rebinding:** https-only, host allowlist, and per-hop public-IP
  re-resolution on every redirect (`backend/app/fetch/guard.py`,
  `test_fetch_guard.py`).
- **Ingestion bounds:** upload/import byte caps, redirect and response-size caps,
  server-observed content type (`app/config.py`, `app/api/v1/uploads.py`).
- **Import safety:** depth/entry/string limits, typed-locator + normalized-box
  validation, v1→v2 migration, full ID/key regeneration (`app/imports/`).
- **Response hardening:** strict security headers on every response — CSP
  `default-src 'none'`, `nosniff`, `frame-options DENY`, `no-referrer`, COOP/CORP,
  permissions policy; HSTS in production only (`app/middleware.py`).
- **Rate limiting:** best-effort per-replica limiter, enabled per-deployment
  (`XRAY_RATE_LIMIT_PER_MINUTE`).
- **XSS:** the SPA renders all extracted/imported text as React text nodes; no
  `dangerouslySetInnerHTML` / `innerHTML` / `eval` sinks exist in the frontend.
- **Deletion:** synchronous access revocation + idempotent purge leaving a
  content-free audit tombstone (`app/services/deletion.py`, `test_deletion.py`).
- **Supply chain:** CI runs `pip-audit` + `npm audit` and emits CycloneDX SBOMs
  (`.github/workflows/ci.yml`).
- **Secrets:** only in the managed secret store; rotation procedure in RUNBOOK §6.

## Deployment responsibilities (not enforced in code)

- **SPA Content-Security-Policy** must be set as a **response header at the static
  host** serving the built frontend (a strict policy that permits the app's own
  scripts/styles and the PDF.js worker's `worker-src`/`blob:` needs). It is not a
  `<meta>` tag in `index.html`, so it can be tuned per host without a rebuild.
- **TLS everywhere** (the API asserts HSTS only when `XRAY_ENVIRONMENT=production`).
- **Network policy** enforcing the worker egress rules above (RUNBOOK §7).
- **Migrations** run as a discrete deploy step, not by every replica (RUNBOOK §4).

## Reporting a vulnerability

Report privately to the maintainer (`gdewalt@gmail.com`) — do not open a public
issue for a suspected vulnerability. Include reproduction steps and impact. As a
single-maintainer project there is no formal SLA, but reports are triaged
promptly and fixes prioritized by severity.
