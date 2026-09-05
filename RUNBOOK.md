# Operations Runbook

Operational procedures for running X-Ray Spec in production. Scope is the
single-provider topology in [DESIGN.md §20](DESIGN.md): stateless API replicas, an
isolated extraction worker (no egress), a restricted-egress fetch worker, managed
PostgreSQL with point-in-time recovery (PITR) — the system of record **and** the
job queue — and managed private object storage.

These procedures satisfy the Phase-5 launch gates in DESIGN.md §19.6–19.7: a
documented, rehearsed restore; a deletion-audit drill; and dependency/image
scanning (the `security` job in `.github/workflows/ci.yml`).

---

## 1. Health and readiness

| Probe | Endpoint | Meaning |
| --- | --- | --- |
| Liveness | `GET /live` | Process is up. Restart the replica if this fails. |
| Readiness | `GET /ready` | Database **and** object store are reachable. `503` ⇒ pull the replica from rotation; it cannot serve. |

`/ready` returns per-dependency status (`database`, `storage`). It performs no
authentication and leaks no content, so it is safe as a load-balancer probe.

---

## 2. Backup

Two independent stores must be backed up; a restore is only valid if they are
**consistent with each other** (see §3).

### 2.1 PostgreSQL (system of record + job queue)

- **PITR is the primary mechanism.** Enable continuous archiving / PITR on the
  managed instance (retention ≥ the deletion/retention window, default 90 days —
  `XRAY_DEFAULT_RETENTION_DAYS`).
- Take a daily base backup in addition to PITR so a restore does not have to
  replay weeks of WAL.
- Verify backups are actually being produced weekly (check the provider's backup
  timestamps; a backup job that silently stopped is the classic failure).

### 2.2 Object storage (source PDFs, immutable artifacts, exports)

- Enable **versioning** on the bucket so an object is never lost to an overwrite
  or an errant delete, and set a lifecycle rule matching the retention window.
- If the provider supports it, enable cross-region replication or scheduled
  bucket snapshots for disaster recovery.
- Object keys are server-generated and content-addressed; the database is the
  index into them. A bucket restored without its matching database is unusable —
  restore them as a pair.

### 2.3 Secrets

Secrets live only in the managed secret store (§20), never in the database or
object store. They are **not** part of the data backup; they are recreated from
the secret store during a restore. Keep the secret store's own backup/export in
the provider.

---

## 3. Restore drill (rehearse before launch, then quarterly)

Restore into a **fresh, isolated environment** — never over production.

1. **Pick a recovery point.** Choose a timestamp `T` (e.g. just before an
   incident). Both stores are restored to `T` so they agree.
2. **Restore PostgreSQL** to `T` via PITR into a new instance.
3. **Restore object storage** to `T`: if using versioning, roll objects back to
   the version current at `T`; if using snapshots, restore the snapshot nearest
   `≤ T`.
4. **Provision secrets** in the new environment from the secret store (database
   URL, storage credentials, `XRAY_SUPABASE_JWT_SECRET`, allowlist).
5. **Point a canary API replica** at the restored stores. Run migrations to
   `head` only if the restore predates the current schema (see §4).
6. **Verify consistency:**
   - `GET /ready` returns `200` with both dependencies `ok`.
   - For a sample of `UserDocument` rows, the referenced source object and active
     artifact object both exist in the restored bucket (no dangling index rows).
   - For a sample of bucket objects that should be live, a matching non-deleted
     row exists (no orphaned blobs that deletion should have purged).
   - A previously `ready` document opens end-to-end (entries load, source PDF
     streams).
7. **Record** the recovery point, wall-clock duration, and any inconsistency in
   the drill log. A drill that finds a dangling reference is a success — fix the
   backup ordering so both stores are captured close together.

**RPO / RTO:** RPO is bounded by WAL archive frequency (Postgres) and bucket
versioning (storage). RTO is the measured drill duration. Update the target
numbers in DESIGN.md §19.2 once a drill has produced real figures.

---

## 4. Migrations

- Migrations are **not** run opportunistically by every replica (§20). Run
  `alembic upgrade head` as a **discrete deploy step**, from one place, before
  rolling API/worker images that expect the new schema.
- Migrations are forward-only in production. To reverse a bad migration, restore
  via PITR (§3) rather than running `downgrade` against live data.
- Baseline/rollback revisions live in `backend/migrations/versions/`. CI runs the
  migration round-trip test (`tests/test_migrations.py`).

---

## 5. Deletion-audit drill (§9.4, §19.6)

Deletion revokes access synchronously, then an **idempotent purge**
(`backend/app/services/deletion.py`) removes bookmarks, annotations, jobs,
immutable artifacts (and their blobs), and the source PDF + row when it is no
longer shared — leaving a **content-free audit tombstone**. Rehearse it:

1. Create a document (fetch or upload), let it reach `ready`, add a bookmark and
   an annotation.
2. Record the source object key and artifact object key(s) from the database.
3. `DELETE /api/v1/documents/{id}`. Confirm the response and that the document is
   immediately inaccessible to its owner (`404`).
4. Run the purge to completion (it is safe to retry). Confirm:
   - the `UserDocument`, bookmarks, annotations, overrides, and jobs are gone;
   - the artifact and source objects are absent from the bucket (when unshared);
   - an `AuditEvent` tombstone remains and contains **no** document content
     (no title, no text, no filename) — only opaque IDs, actor, and timestamp.
5. Re-run the purge and confirm it is a no-op (idempotence).
6. Confirm a restore from a backup taken **after** the deletion does not bring
   the content back.

---

## 6. Secret rotation

Rotate on a schedule and immediately on suspected exposure.

- **`XRAY_SUPABASE_JWT_SECRET`** — rotating invalidates all issued sessions
  (users re-authenticate). Coordinate with the Supabase project's JWT settings so
  verification and issuance use the same secret; roll it in the secret store, then
  restart API replicas so `get_settings()` (LRU-cached) re-reads it.
- **`XRAY_SUPABASE_SERVICE_KEY` / storage credentials** — rotate in the provider,
  update the secret store, restart the replicas and the workers that hold a
  cached client.
- **Database credentials** — rotate in the managed instance, update the secret
  store, roll replicas/workers.
- After any rotation, verify with `GET /ready` and one end-to-end document open.

---

## 7. Egress and network posture (verify after any infra change)

- **Extraction worker: no egress.** Confirm the network policy blocks all
  outbound traffic. It parses untrusted PDFs and runs OCR; it must not be able to
  reach the internet or internal services.
- **Fetch worker: allowlisted egress only.** Outbound fetches pass the SSRF guard
  (`backend/app/fetch/guard.py`): https-only, host on
  `XRAY_FETCH_ALLOWED_HOSTS`, and every hop re-resolved so **all** IPs are public
  (defeats DNS rebinding). Redirects are bounded by `XRAY_FETCH_MAX_REDIRECTS`.
  Covered by `tests/test_fetch_guard.py`.
- **API replicas** sit behind the managed edge/TLS. Set
  `XRAY_RATE_LIMIT_PER_MINUTE` to a non-zero budget and, only if a trusted proxy
  sets `X-Forwarded-For`, `XRAY_TRUST_FORWARDED_FOR=true` so the limiter keys on
  the real client. `strict-transport-security` is asserted only when
  `XRAY_ENVIRONMENT=production`.

---

## 8. Incident response (quick reference)

| Symptom | First checks |
| --- | --- |
| Replicas failing `/ready` | Which dependency? DB vs storage. Provider status; credentials rotated but replicas not restarted (cached settings). |
| Jobs stuck / not progressing | Worker liveness; lease reaper running; `GET /jobs/{id}` snapshot; a crashed worker's lease should expire and be re-claimed, not duplicated. |
| Suspected data exposure | Rotate the relevant secret (§6); review `AuditEvent`s; restore-to-fresh (§3) if integrity is in doubt — never debug on production data. |
| Dependency CVE reported | Re-run the `security` CI job; bump the pinned dependency; if no fix exists, assess exploitability and document the decision. |
| Bad deploy | Roll back the image. For a bad **migration**, restore via PITR (§4) — do not `downgrade` live data. |

Golden rule: **restore into a fresh environment, verify, then cut over.** Never
run recovery experiments against production stores.
