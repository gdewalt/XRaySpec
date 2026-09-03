import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type {
  ArtifactEntries,
  AssociationDto,
  DocumentRead,
  EntryDto,
  FigureMentionDto,
  Locator,
  NumeralMentionDto,
} from "../api/types";

function refShort(loc: Locator): string {
  return loc.kind === "grant" ? `${loc.column}:${loc.printed_line}` : `[${loc.paragraph}]`;
}

function refLong(loc: Locator): string {
  return loc.kind === "grant"
    ? `col. ${loc.column}, l. ${loc.printed_line}`
    : `¶ [${loc.paragraph}]`;
}

type Mark = { start: number; end: number; cls: string; title: string };

function buildMarks(
  entry: EntryDto,
  figs: FigureMentionDto[],
  nums: NumeralMentionDto[],
  assoc: Map<string, AssociationDto>,
): Mark[] {
  const text = entry.source_text;
  const marks: Mark[] = [];
  for (const f of figs) {
    marks.push({
      start: f.span[0],
      end: f.span[1],
      cls: "mention figure",
      title: `Figure reference → ${f.figure_ids.join(", ")}`,
    });
  }
  for (const n of nums) {
    const a = assoc.get(`${n.entry_id}:${n.span[0]}:${n.span[1]}`);
    const status = a?.status ?? "unresolved";
    const label = n.component_label ? ` (${n.component_label})` : "";
    marks.push({
      start: n.span[0],
      end: n.span[1],
      cls: `mention numeral ${status}`,
      title: `Reference numeral ${n.value}${label} — ${status}`,
    });
  }
  return marks.filter((m) => m.start >= 0 && m.start < m.end && m.end <= text.length).sort(
    (a, b) => a.start - b.start,
  );
}

function groupByEntry<T extends { entry_id: string }>(items: T[]): Map<string, T[]> {
  const map = new Map<string, T[]>();
  for (const item of items) {
    const arr = map.get(item.entry_id);
    if (arr) arr.push(item);
    else map.set(item.entry_id, [item]);
  }
  return map;
}

function renderText(entry: EntryDto, marks: Mark[]): ReactNode {
  const text = entry.source_text;
  const nodes: ReactNode[] = [];
  let pos = 0;
  let key = 0;
  for (const m of marks) {
    if (m.start < pos) continue; // overlapping mention — skip
    if (m.start > pos) nodes.push(<span key={key++}>{text.slice(pos, m.start)}</span>);
    nodes.push(
      <mark key={key++} className={m.cls} title={m.title}>
        {text.slice(m.start, m.end)}
      </mark>,
    );
    pos = m.end;
  }
  if (pos < text.length) nodes.push(<span key={key++}>{text.slice(pos)}</span>);
  return nodes;
}

export function Viewer({ documentId, onBack }: { documentId: string; onBack: () => void }) {
  const [doc, setDoc] = useState<DocumentRead | null>(null);
  const [artifact, setArtifact] = useState<ArtifactEntries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<EntryDto | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await api.getDocument(documentId);
        if (cancelled) return;
        setDoc(d);
        if (d.active_artifact_id) {
          const a = await api.getArtifactEntries(d.active_artifact_id);
          if (!cancelled) setArtifact(a);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  const figsByEntry = useMemo(() => groupByEntry(artifact?.figure_mentions ?? []), [artifact]);
  const numsByEntry = useMemo(() => groupByEntry(artifact?.numeral_mentions ?? []), [artifact]);

  const assocByKey = useMemo(() => {
    const map = new Map<string, AssociationDto>();
    for (const a of artifact?.mention_associations ?? []) {
      map.set(`${a.entry_id}:${a.span[0]}:${a.span[1]}`, a);
    }
    return map;
  }, [artifact]);

  const copy = useCallback(async (kind: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      setError("Copy failed (clipboard unavailable).");
    }
  }, []);

  const citation = selected && doc ? `${doc.title}, ${refLong(selected.locator)}` : "";

  return (
    <div className="viewer">
      <div className="viewer-bar">
        <button type="button" className="secondary" onClick={onBack}>
          ← Documents
        </button>
        <strong className="viewer-title">{doc?.title ?? "…"}</strong>
        <div className="tabs">
          <button
            type="button"
            className={`tab${showDetails ? "" : " active"}`}
            onClick={() => setShowDetails(false)}
          >
            Text
          </button>
          <button
            type="button"
            className={`tab${showDetails ? " active" : ""}`}
            onClick={() => setShowDetails(true)}
          >
            Details
          </button>
        </div>
      </div>

      {error && (
        <p className="status err" role="alert">
          {error}
        </p>
      )}

      {!artifact && !error && (
        <p className="muted viewer-empty">
          {doc && !doc.active_artifact_id
            ? "This document has no extracted artifact yet."
            : "Loading…"}
        </p>
      )}

      {artifact && showDetails && (
        <section className="panel">
          <h2>Details</h2>
          <dl className="details">
            <dt>Type</dt>
            <dd>{artifact.doc_type}</dd>
            <dt>Mode</dt>
            <dd>{artifact.mode}</dd>
            <dt>Disposition</dt>
            <dd>{artifact.disposition}</dd>
            <dt>Pages</dt>
            <dd>{artifact.page_count}</dd>
            <dt>Lines</dt>
            <dd>{artifact.entries.length}</dd>
            <dt>Figure references</dt>
            <dd>{artifact.figure_mentions.length}</dd>
            <dt>Reference numerals</dt>
            <dd>{artifact.numeral_mentions.length}</dd>
            <dt>Drawing callouts</dt>
            <dd>{artifact.callout_occurrences.length}</dd>
          </dl>
        </section>
      )}

      {artifact && !showDetails && (
        <section className="spec" aria-label="Specification text">
          {artifact.entries.map((e) => {
            const marks = buildMarks(
              e,
              figsByEntry.get(e.entry_id) ?? [],
              numsByEntry.get(e.entry_id) ?? [],
              assocByKey,
            );
            return (
              <div
                key={e.entry_id}
                className={`spec-line${selected?.entry_id === e.entry_id ? " selected" : ""}`}
                onClick={() => setSelected(e)}
              >
                <span className={`conf ${e.text_confidence}`} title={`text: ${e.text_confidence}`} />
                <span className="ref" title={refLong(e.locator)}>
                  {refShort(e.locator)}
                </span>
                <span className="line-text">{renderText(e, marks)}</span>
              </div>
            );
          })}
        </section>
      )}

      {selected && (
        <div className="cite-bar" role="region" aria-label="Copy and cite">
          <span className="cite-ref">{citation}</span>
          <div className="cite-actions">
            <button type="button" onClick={() => copy("text", selected.display_text)}>
              Copy text
            </button>
            <button type="button" onClick={() => copy("cite", citation)}>
              Copy citation
            </button>
            <button
              type="button"
              onClick={() => copy("both", `“${selected.display_text}” ${citation}`)}
            >
              Copy text + citation
            </button>
            {copied && <span className="copied">Copied {copied}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
