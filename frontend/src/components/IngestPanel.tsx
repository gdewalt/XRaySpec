import { type ChangeEvent, type FormEvent, type KeyboardEvent, useState } from "react";

import { api, uploadPdf } from "../api/client";
import type { ImportAnalysis } from "../api/types";
import { Icon, type IconName } from "./Icon";

type Msg = { kind: "ok" | "err"; text: string } | null;

function toMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function Status({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return (
    <p className={`status ${msg.kind}`} role={msg.kind === "err" ? "alert" : "status"}>
      {msg.text}
    </p>
  );
}

function FetchForm({ onChanged }: { onChanged: () => void }) {
  const [identifier, setIdentifier] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const doc = await api.createFetch(identifier.trim(), title.trim() || undefined);
      setMsg({ kind: "ok", text: `Queued “${doc.title}” for fetching.` });
      setIdentifier("");
      setTitle("");
      onChanged();
    } catch (err) {
      setMsg({ kind: "err", text: toMsg(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="fetch-id">US patent or publication number</label>
        <input
          id="fetch-id"
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
          placeholder="e.g. US 12,262,260 B2"
          required
          autoComplete="off"
        />
        <span className="field-hint">Grant and application publication numbers are supported.</span>
      </div>
      <div className="field">
        <label htmlFor="fetch-title">Display title <span className="optional">Optional</span></label>
        <input
          id="fetch-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Uses the patent title when left blank"
        />
      </div>
      <div className="form-actions">
        <button type="submit" disabled={busy || !identifier.trim()}>
          <Icon name="search" size={17} /> {busy ? "Queuing…" : "Fetch patent"}
        </button>
      </div>
      <Status msg={msg} />
    </form>
  );
}

function UploadForm({ onChanged }: { onChanged: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await uploadPdf(file, title.trim() || undefined);
      setMsg({ kind: "ok", text: `Uploaded “${res.document.title}” (job ${res.job_status}).` });
      setFile(null);
      setTitle("");
      onChanged();
    } catch (err) {
      setMsg({ kind: "err", text: toMsg(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="upload-file">Source PDF</label>
        <label className={`file-drop${file ? " has-file" : ""}`} htmlFor="upload-file">
          <span className="file-icon" aria-hidden="true"><Icon name="file" size={22} /></span>
          <span>
            <strong>{file ? file.name : "Choose a patent PDF"}</strong>
            <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB selected` : "PDF files up to 50 MB"}</small>
          </span>
          <span className="file-action">Browse</span>
        </label>
        <input
          className="visually-hidden-input"
          id="upload-file"
          type="file"
          accept="application/pdf,.pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          required
        />
      </div>
      <div className="field">
        <label htmlFor="upload-title">Display title <span className="optional">Optional</span></label>
        <input id="upload-title" value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="form-actions">
        <button type="submit" disabled={busy || !file}>
          <Icon name="upload" size={17} /> {busy ? "Uploading…" : "Upload and process"}
        </button>
      </div>
      <Status msg={msg} />
    </form>
  );
}

function ImportForm({ onChanged }: { onChanged: () => void }) {
  const [analysis, setAnalysis] = useState<ImportAnalysis | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setBusy(true);
    setMsg(null);
    setAnalysis(null);
    setConfirm(false);
    try {
      setAnalysis(await api.analyzeImport(await f.arrayBuffer()));
    } catch (err) {
      setMsg({ kind: "err", text: toMsg(err) });
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!analysis) return;
    setBusy(true);
    setMsg(null);
    try {
      const doc = await api.commitImport(analysis.import_id, { confirm_migration: confirm });
      setMsg({ kind: "ok", text: `Imported “${doc.title}”.` });
      setAnalysis(null);
      onChanged();
    } catch (err) {
      setMsg({ kind: "err", text: toMsg(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="form">
      <div className="field">
        <label htmlFor="import-file">Portable save</label>
        <label className="file-drop" htmlFor="import-file">
          <span className="file-icon json" aria-hidden="true"><Icon name="import" size={22} /></span>
          <span><strong>Choose a portable save</strong><small>.patent-viewer.json files</small></span>
          <span className="file-action">Browse</span>
        </label>
        <input className="visually-hidden-input" id="import-file" type="file" accept="application/json,.json" onChange={onFile} />
      </div>
      {analysis && (
        <div className="analysis">
          <p>
            <strong>{analysis.entry_count}</strong> entries,{" "}
            <strong>{analysis.bookmark_count}</strong> bookmarks
            {analysis.doc_type ? ` · ${analysis.doc_type}` : ""} · schema v
            {analysis.schema_version}
          </p>
          {analysis.warnings.length > 0 && (
            <ul className="warnings">
              {analysis.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          {analysis.needs_migration && (
            <label className="checkbox">
              <input
                type="checkbox"
                checked={confirm}
                onChange={(e) => setConfirm(e.target.checked)}
              />
              Confirm migration of this legacy version-1 save
            </label>
          )}
          <button
            type="button"
            onClick={commit}
            disabled={busy || (analysis.needs_migration && !confirm)}
          >
            <Icon name="check" size={17} /> {busy ? "Importing…" : "Import save"}
          </button>
        </div>
      )}
      <Status msg={msg} />
    </div>
  );
}

const TABS: ReadonlyArray<{
  id: "fetch" | "upload" | "import";
  label: string;
  icon: IconName;
}> = [
  { id: "fetch", label: "Patent number", icon: "search" },
  { id: "upload", label: "Upload PDF", icon: "upload" },
  { id: "import", label: "Import", icon: "import" },
];

type Tab = (typeof TABS)[number]["id"];

export function IngestPanel({ onChanged }: { onChanged: () => void }) {
  const [tab, setTab] = useState<Tab>("fetch");

  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nextIndex =
      event.key === "Home" ? 0 :
      event.key === "End" ? TABS.length - 1 :
      (index + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    const next = TABS[nextIndex];
    setTab(next.id);
    document.getElementById(`tab-${next.id}`)?.focus();
  }

  return (
    <section className="panel ingest-panel" aria-labelledby="ingest-h">
      <div className="panel-heading-copy">
        <span className="panel-heading-icon" aria-hidden="true"><Icon name="plus" size={20} /></span>
        <div>
          <p className="eyebrow">Add evidence</p>
          <h2 id="ingest-h">Add a patent</h2>
          <p>Start with a public identifier, source PDF, or portable save.</p>
        </div>
      </div>
      <div role="tablist" aria-label="Ingestion method" className="tabs">
        {TABS.map((item, index) => (
          <button
            key={item.id}
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={tab === item.id}
            aria-controls={`panel-${item.id}`}
            tabIndex={tab === item.id ? 0 : -1}
            className={`tab${tab === item.id ? " active" : ""}`}
            onClick={() => setTab(item.id)}
            onKeyDown={(event) => navigateTabs(event, index)}
            type="button"
          >
            <Icon name={item.icon} size={16} />
            {item.label}
          </button>
        ))}
      </div>
      <div className="tab-panel" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "fetch" && <FetchForm onChanged={onChanged} />}
        {tab === "upload" && <UploadForm onChanged={onChanged} />}
        {tab === "import" && <ImportForm onChanged={onChanged} />}
      </div>
    </section>
  );
}
