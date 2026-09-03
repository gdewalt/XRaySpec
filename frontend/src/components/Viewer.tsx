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
import { PdfPane } from "./PdfPane";

type Layout = "text" | "pdf" | "split" | "details";
type Selection = { start: number; end: number } | null;

function refRange(entries: EntryDto[]): string {
  if (entries.length === 0) return "";
  const a = entries[0].locator;
  const b = entries[entries.length - 1].locator;
  if (a.kind === "grant" && b.kind === "grant") {
    if (a.column === b.column) {
      return a.printed_line === b.printed_line
        ? `col. ${a.column}, l. ${a.printed_line}`
        : `col. ${a.column}, ll. ${a.printed_line}–${b.printed_line}`;
    }
    return `col. ${a.column}, l. ${a.printed_line} – col. ${b.column}, l. ${b.printed_line}`;
  }
  if (a.paragraph && b.paragraph) {
    return a.paragraph === b.paragraph
      ? `¶ [${a.paragraph}]`
      : `¶¶ [${a.paragraph}]–[${b.paragraph}]`;
  }
  return "";
}

function refShort(loc: Locator): string {
  return loc.kind === "grant" ? `${loc.column}:${loc.printed_line}` : `[${loc.paragraph}]`;
}

type Mark = { start: number; end: number; cls: string; title: string };

function groupByEntry<T extends { entry_id: string }>(items: T[]): Map<string, T[]> {
  const map = new Map<string, T[]>();
  for (const item of items) {
    const arr = map.get(item.entry_id);
    if (arr) arr.push(item);
    else map.set(item.entry_id, [item]);
  }
  return map;
}

function buildMarks(
  entry: EntryDto,
  figs: FigureMentionDto[],
  nums: NumeralMentionDto[],
  assoc: Map<string, AssociationDto>,
): Mark[] {
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
    const status = assoc.get(`${n.entry_id}:${n.span[0]}:${n.span[1]}`)?.status ?? "unresolved";
    const label = n.component_label ? ` (${n.component_label})` : "";
    marks.push({
      start: n.span[0],
      end: n.span[1],
      cls: `mention numeral ${status}`,
      title: `Reference numeral ${n.value}${label} — ${status}`,
    });
  }
  const len = entry.source_text.length;
  return marks.filter((m) => m.start >= 0 && m.start < m.end && m.end <= len).sort(
    (a, b) => a.start - b.start,
  );
}

function renderText(entry: EntryDto, marks: Mark[]): ReactNode {
  const text = entry.source_text;
  const nodes: ReactNode[] = [];
  let pos = 0;
  let key = 0;
  for (const m of marks) {
    if (m.start < pos) continue;
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
  const [layout, setLayout] = useState<Layout>("text");
  const [selection, setSelection] = useState<Selection>(null);
  const [pdfPage, setPdfPage] = useState(1);
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

  const entries = artifact?.entries ?? [];
  const figsByEntry = useMemo(() => groupByEntry(artifact?.figure_mentions ?? []), [artifact]);
  const numsByEntry = useMemo(() => groupByEntry(artifact?.numeral_mentions ?? []), [artifact]);
  const assocByKey = useMemo(() => {
    const map = new Map<string, AssociationDto>();
    for (const a of artifact?.mention_associations ?? []) {
      map.set(`${a.entry_id}:${a.span[0]}:${a.span[1]}`, a);
    }
    return map;
  }, [artifact]);

  const selectLine = useCallback((e: EntryDto) => {
    setSelection({ start: e.ordinal, end: e.ordinal });
    setPdfPage(e.page_index + 1);
  }, []);

  const selectedEntries = useMemo(
    () =>
      selection
        ? entries.filter((e) => e.ordinal >= selection.start && e.ordinal <= selection.end)
        : [],
    [selection, entries],
  );

  const copy = useCallback(async (kind: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      setError("Copy failed (clipboard unavailable).");
    }
  }, []);

  const citation =
    selectedEntries.length && doc ? `${doc.title}, ${refRange(selectedEntries)}` : "";
  const selectedText = selectedEntries.map((e) => e.display_text).join(" ");
  const highlightOrdinal = selection ? selection.start : null;

  const showText = layout === "text" || layout === "split";
  const showPdf = layout === "pdf" || layout === "split";

  const tabs: Layout[] = ["text", "pdf", "split", "details"];

  return (
    <div className="viewer">
      <div className="viewer-bar">
        <button type="button" className="secondary" onClick={onBack}>
          ← Documents
        </button>
        <strong className="viewer-title">{doc?.title ?? "…"}</strong>
        <div className="tabs" role="tablist" aria-label="Layout">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={layout === t}
              className={`tab${layout === t ? " active" : ""}`}
              onClick={() => setLayout(t)}
            >
              {t === "pdf" ? "PDF" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <p className="status err" role="alert">
          {error}
        </p>
      )}

      {!artifact && !error && (
        <p className="muted viewer-empty">
          {doc && !doc.active_artifact_id ? "No extracted artifact yet." : "Loading…"}
        </p>
      )}

      {artifact && layout === "details" && (
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
            <dd>{entries.length}</dd>
            <dt>Figure references</dt>
            <dd>{artifact.figure_mentions.length}</dd>
            <dt>Reference numerals</dt>
            <dd>{artifact.numeral_mentions.length}</dd>
            <dt>Drawing callouts</dt>
            <dd>{artifact.callout_occurrences.length}</dd>
          </dl>
        </section>
      )}

      {artifact && layout !== "details" && (
        <div className={`panes ${layout}`}>
          {showText && (
            <section className="spec" aria-label="Specification text">
              {entries.map((e) => {
                const marks = buildMarks(
                  e,
                  figsByEntry.get(e.entry_id) ?? [],
                  numsByEntry.get(e.entry_id) ?? [],
                  assocByKey,
                );
                const sel =
                  selection && e.ordinal >= selection.start && e.ordinal <= selection.end;
                return (
                  <div
                    key={e.entry_id}
                    className={`spec-line${sel ? " selected" : ""}`}
                    onClick={() => selectLine(e)}
                  >
                    <span className={`conf ${e.text_confidence}`} title={e.text_confidence} />
                    <span className="ref">{refShort(e.locator)}</span>
                    <span className="line-text">{renderText(e, marks)}</span>
                  </div>
                );
              })}
            </section>
          )}
          {showPdf && (
            <PdfPane
              documentId={documentId}
              entries={entries}
              page={pdfPage}
              onPageChange={setPdfPage}
              highlightOrdinal={highlightOrdinal}
              onSelectLine={selectLine}
              onSelectRange={(a, b) => setSelection({ start: a, end: b })}
            />
          )}
        </div>
      )}

      {selectedEntries.length > 0 && (
        <div className="cite-bar" role="region" aria-label="Copy and cite">
          <span className="cite-ref">{citation}</span>
          <div className="cite-actions">
            <button type="button" onClick={() => copy("text", selectedText)}>
              Copy text
            </button>
            <button type="button" onClick={() => copy("cite", citation)}>
              Copy citation
            </button>
            <button type="button" onClick={() => copy("both", `“${selectedText}” ${citation}`)}>
              Copy text + citation
            </button>
            {copied && <span className="copied">Copied {copied}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
