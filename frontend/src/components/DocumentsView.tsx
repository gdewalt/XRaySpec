import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { DocumentRead } from "../api/types";
import { setToken } from "../auth/session";
import { IngestPanel } from "./IngestPanel";
import { JobProgress } from "./JobProgress";

const ACTIVE_STATES = new Set([
  "preparing",
  "processing",
  "pending_fetch",
  "reprocessing",
  "deleting",
]);

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function StateBadge({ state }: { state: string }) {
  return (
    <span className={`badge state-${state}`}>
      <span className="badge-dot" aria-hidden="true" />
      {state.replace(/_/g, " ")}
    </span>
  );
}

export function DocumentsView({ onOpen }: { onOpen: (documentId: string) => void }) {
  const [docs, setDocs] = useState<DocumentRead[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.listDocuments();
      setDocs(res.items);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setToken(null); // token expired/invalid — back to sign-in
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while any document is still working.
  const active = !!docs?.some((d) => ACTIVE_STATES.has(d.state));
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(load, 4000);
    return () => window.clearInterval(id);
  }, [active, load]);

  async function remove(doc: DocumentRead) {
    if (!window.confirm(`Delete “${doc.title}”? This cannot be undone.`)) return;
    try {
      await api.deleteDocument(doc.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const total = docs?.length ?? 0;
  const ready = docs?.filter((d) => d.state === "ready" || d.state === "text_only").length ?? 0;
  const working = docs?.filter((d) => ACTIVE_STATES.has(d.state)).length ?? 0;

  return (
    <div className="dashboard">
      <section className="dashboard-intro" aria-labelledby="workspace-h">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1 id="workspace-h">Your patent library</h1>
          <p className="intro-copy">
            Read specifications beside the source PDF and keep every citation tied to evidence.
          </p>
        </div>
        <dl className="library-stats" aria-label="Library summary">
          <div>
            <dt>Documents</dt>
            <dd>{total}</dd>
          </div>
          <div>
            <dt>Ready</dt>
            <dd>{ready}</dd>
          </div>
          <div>
            <dt>In progress</dt>
            <dd>{working}</dd>
          </div>
        </dl>
      </section>
      <IngestPanel onChanged={load} />
      <section className="panel documents-panel" aria-labelledby="docs-h">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Library</p>
            <h2 id="docs-h">Documents</h2>
          </div>
          <button type="button" className="icon-button" onClick={load} aria-label="Refresh documents">
            <span aria-hidden="true">↻</span>
            <span>Refresh</span>
          </button>
        </div>
        {error && (
          <p className="status err" role="alert">
            {error}
          </p>
        )}
        {docs === null ? (
          <div className="loading-state" role="status">
            <span className="spinner" aria-hidden="true" /> Loading your library…
          </div>
        ) : docs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-illustration" aria-hidden="true">
              <span>§</span>
            </div>
            <h3>Your library is empty</h3>
            <p>Add a US patent number, upload a PDF, or restore a portable save to begin.</p>
          </div>
        ) : (
          <div className="document-list">
            {docs.map((d) => {
              const canOpen = d.state === "ready" || d.state === "text_only";
              return (
                <article className="document-row" key={d.id}>
                  <button
                    type="button"
                    className="document-main"
                    onClick={() => onOpen(d.id)}
                    disabled={!canOpen}
                    aria-label={`${canOpen ? "Open" : "View status for"} ${d.title}`}
                  >
                    <span className="document-icon" aria-hidden="true">US</span>
                    <span className="document-copy">
                      <strong>{d.title}</strong>
                      <span className="document-meta">
                        Added {formatDate(d.created_at)}
                        <span aria-hidden="true">·</span>
                        {d.last_opened_at ? `Opened ${formatDate(d.last_opened_at)}` : "Not opened yet"}
                      </span>
                    </span>
                  </button>
                  <div className="document-status">
                    <StateBadge state={d.state} />
                    {ACTIVE_STATES.has(d.state) && d.state !== "deleting" && (
                      <JobProgress documentId={d.id} onComplete={load} />
                    )}
                  </div>
                  <div className="row-actions">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => onOpen(d.id)}
                      disabled={!canOpen}
                    >
                      Open <span aria-hidden="true">→</span>
                    </button>
                    <button
                      type="button"
                      className="menu-button"
                      onClick={() => remove(d)}
                      aria-label={`Delete ${d.title}`}
                      title="Delete document"
                    >
                      <span aria-hidden="true">•••</span>
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
