import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api/client";
import type { DocumentRead, WorkspaceRead } from "../api/types";
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
    <span className={"badge state-" + state}>
      <span className="badge-dot" aria-hidden="true" />
      {state.replace(/_/g, " ")}
    </span>
  );
}

interface PatentRowProps {
  doc: DocumentRead;
  workspaces: WorkspaceRead[];
  moving: boolean;
  onOpen: (documentId: string) => void;
  onMove: (doc: DocumentRead, workspaceId: string | null) => void;
  onRemove: (doc: DocumentRead) => void;
  onJobComplete: () => void;
}

function PatentRow({
  doc,
  workspaces,
  moving,
  onOpen,
  onMove,
  onRemove,
  onJobComplete,
}: PatentRowProps) {
  const canOpen = doc.state === "ready" || doc.state === "text_only";

  return (
    <article className="document-row">
      <button
        type="button"
        className="document-main"
        onClick={() => onOpen(doc.id)}
        disabled={!canOpen}
        aria-label={(canOpen ? "Open " : "View status for ") + doc.title}
      >
        <span className="document-icon" aria-hidden="true">US</span>
        <span className="document-copy">
          <strong>{doc.title}</strong>
          <span className="document-meta">
            Added {formatDate(doc.created_at)}
            <span aria-hidden="true">·</span>
            {doc.last_opened_at ? "Opened " + formatDate(doc.last_opened_at) : "Not opened yet"}
          </span>
        </span>
      </button>
      <div className="document-status">
        <StateBadge state={doc.state} />
        {ACTIVE_STATES.has(doc.state) && doc.state !== "deleting" && (
          <JobProgress documentId={doc.id} onComplete={onJobComplete} />
        )}
      </div>
      <div className="row-actions">
        <label className="move-control">
          <span className="sr-only">Move {doc.title} to workspace</span>
          <select
            value={doc.workspace_id ?? ""}
            onChange={(event) => onMove(doc, event.target.value || null)}
            disabled={moving}
            aria-label={"Move " + doc.title + " to workspace"}
          >
            <option value="">Unfiled</option>
            {workspaces.map((workspace) => (
              <option value={workspace.id} key={workspace.id}>
                {workspace.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="secondary"
          onClick={() => onOpen(doc.id)}
          disabled={!canOpen}
        >
          Open <span aria-hidden="true">→</span>
        </button>
        <button
          type="button"
          className="menu-button"
          onClick={() => onRemove(doc)}
          aria-label={"Delete " + doc.title}
          title="Delete patent"
        >
          <span aria-hidden="true">•••</span>
        </button>
      </div>
    </article>
  );
}

export function DocumentsView({ onOpen }: { onOpen: (documentId: string) => void }) {
  const [docs, setDocs] = useState<DocumentRead[] | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceRead[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [workspaceName, setWorkspaceName] = useState("");
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);
  const [movingId, setMovingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [documentResult, workspaceResult] = await Promise.all([
        api.listDocuments(),
        api.listWorkspaces(),
      ]);
      setDocs(documentResult.items);
      setWorkspaces(workspaceResult.items);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setToken(null);
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const active = !!docs?.some((doc) => ACTIVE_STATES.has(doc.state));
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(load, 4000);
    return () => window.clearInterval(id);
  }, [active, load]);

  const groups = useMemo(() => {
    if (!docs) return [];
    return [
      {
        id: "unfiled",
        name: "Unfiled",
        documents: docs.filter((doc) => doc.workspace_id === null),
      },
      ...workspaces.map((workspace) => ({
        id: workspace.id,
        name: workspace.name,
        documents: docs.filter((doc) => doc.workspace_id === workspace.id),
      })),
    ];
  }, [docs, workspaces]);

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = workspaceName.trim();
    if (!name) return;
    setCreatingWorkspace(true);
    try {
      const workspace = await api.createWorkspace(name);
      setWorkspaceName("");
      setCollapsed((current) => {
        const next = new Set(current);
        next.delete(workspace.id);
        return next;
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreatingWorkspace(false);
    }
  }

  async function move(doc: DocumentRead, workspaceId: string | null) {
    setMovingId(doc.id);
    try {
      await api.moveDocument(doc.id, workspaceId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setMovingId(null);
    }
  }

  async function remove(doc: DocumentRead) {
    if (!window.confirm("Delete “" + doc.title + "”? This cannot be undone.")) return;
    try {
      await api.deleteDocument(doc.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function toggleGroup(id: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const total = docs?.length ?? 0;
  const ready = docs?.filter((doc) => doc.state === "ready" || doc.state === "text_only").length ?? 0;
  const working = docs?.filter((doc) => ACTIVE_STATES.has(doc.state)).length ?? 0;

  return (
    <div className="dashboard">
      <section className="dashboard-intro" aria-labelledby="workspace-h">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1 id="workspace-h">Your patent library</h1>
          <p className="intro-copy">
            Read specifications beside the source PDF and organize related patents into workspaces.
          </p>
        </div>
        <dl className="library-stats" aria-label="Library summary">
          <div>
            <dt>Patents</dt>
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
            <h2 id="docs-h">Patents</h2>
          </div>
          <button type="button" className="icon-button" onClick={load} aria-label="Refresh patents">
            <span aria-hidden="true">↻</span>
            <span>Refresh</span>
          </button>
        </div>
        <form className="workspace-create" onSubmit={createWorkspace}>
          <label htmlFor="workspace-name">Create a workspace</label>
          <div>
            <input
              id="workspace-name"
              value={workspaceName}
              onChange={(event) => setWorkspaceName(event.target.value)}
              placeholder="e.g. Battery cooling systems"
              maxLength={120}
            />
            <button type="submit" disabled={creatingWorkspace || !workspaceName.trim()}>
              {creatingWorkspace ? "Creating…" : "Create"}
            </button>
          </div>
        </form>
        {error && (
          <p className="status err library-error" role="alert">
            {error}
          </p>
        )}
        {docs === null ? (
          <div className="loading-state" role="status">
            <span className="spinner" aria-hidden="true" /> Loading your library…
          </div>
        ) : docs.length === 0 && workspaces.length === 0 ? (
          <div className="empty-state">
            <div className="empty-illustration" aria-hidden="true">
              <span>§</span>
            </div>
            <h3>Your library is empty</h3>
            <p>Add a US patent number, upload a PDF, or create a workspace to begin.</p>
          </div>
        ) : (
          <div className="workspace-list">
            {groups.map((group) => {
              const isCollapsed = collapsed.has(group.id);
              const regionId = "workspace-" + group.id;
              return (
                <section className="workspace-group" key={group.id}>
                  <button
                    type="button"
                    className="workspace-toggle"
                    onClick={() => toggleGroup(group.id)}
                    aria-expanded={!isCollapsed}
                    aria-controls={regionId}
                  >
                    <span className={"workspace-chevron" + (isCollapsed ? "" : " expanded")} aria-hidden="true">
                      ›
                    </span>
                    <span className="workspace-folder" aria-hidden="true">▰</span>
                    <span className="workspace-name">{group.name}</span>
                    <span className="workspace-count">
                      {group.documents.length} {group.documents.length === 1 ? "patent" : "patents"}
                    </span>
                  </button>
                  {!isCollapsed && (
                    <div id={regionId} className="document-list">
                      {group.documents.length === 0 ? (
                        <p className="workspace-empty">
                          Move a patent here using its workspace menu.
                        </p>
                      ) : (
                        group.documents.map((doc) => (
                          <PatentRow
                            key={doc.id}
                            doc={doc}
                            workspaces={workspaces}
                            moving={movingId === doc.id}
                            onOpen={onOpen}
                            onMove={move}
                            onRemove={remove}
                            onJobComplete={load}
                          />
                        ))
                      )}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
