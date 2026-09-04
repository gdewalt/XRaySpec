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
  return <span className={`badge state-${state}`}>{state.replace(/_/g, " ")}</span>;
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

  return (
    <>
      <IngestPanel onChanged={load} />
      <section className="panel" aria-labelledby="docs-h">
        <div className="panel-head">
          <h2 id="docs-h">Documents</h2>
          <button type="button" className="secondary" onClick={load}>
            Refresh
          </button>
        </div>
        {error && (
          <p className="status err" role="alert">
            {error}
          </p>
        )}
        {docs === null ? (
          <p className="muted">Loading…</p>
        ) : docs.length === 0 ? (
          <p className="muted">No documents yet. Add one above.</p>
        ) : (
          <table className="docs">
            <caption className="sr-only">Your documents</caption>
            <thead>
              <tr>
                <th scope="col">Title</th>
                <th scope="col">Status</th>
                <th scope="col">Created</th>
                <th scope="col">Last opened</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>{d.title}</td>
                  <td>
                    <StateBadge state={d.state} />
                    {ACTIVE_STATES.has(d.state) && d.state !== "deleting" && (
                      <JobProgress documentId={d.id} onComplete={load} />
                    )}
                  </td>
                  <td>{formatDate(d.created_at)}</td>
                  <td>{formatDate(d.last_opened_at)}</td>
                  <td className="row-actions">
                    <button type="button" className="secondary" onClick={() => onOpen(d.id)}>
                      Open
                    </button>
                    <button type="button" className="danger" onClick={() => remove(d)}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
