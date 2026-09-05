import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import type {
  ArtifactEntries,
  AssociationDto,
  DocumentRead,
  EntryDto,
  FigureMentionDto,
  NumeralMentionDto,
} from "../api/types";
import {
  buildViewHash,
  detectOutline,
  highlightSegments,
  parseViewHash,
  refShort,
  searchEntries,
} from "../spec/navigation";
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
  return marks
    .filter((m) => m.start >= 0 && m.start < m.end && m.end <= len)
    .sort((a, b) => a.start - b.start);
}

/** Plain text with case-insensitive search matches wrapped in <mark class="search-hit">. */
function renderPlain(text: string, query: string, keyBase: string): ReactNode[] {
  if (!query) return [text];
  return highlightSegments(text, query).map((seg, i) =>
    seg.hit ? (
      <mark key={`${keyBase}s${i}`} className="search-hit">
        {seg.text}
      </mark>
    ) : (
      <span key={`${keyBase}s${i}`}>{seg.text}</span>
    ),
  );
}

function renderText(entry: EntryDto, marks: Mark[], query: string): ReactNode {
  const text = entry.source_text;
  const nodes: ReactNode[] = [];
  let pos = 0;
  let key = 0;
  for (const m of marks) {
    if (m.start < pos) continue;
    if (m.start > pos) nodes.push(...renderPlain(text.slice(pos, m.start), query, `p${key++}`));
    nodes.push(
      <mark key={`m${key++}`} className={m.cls} title={m.title}>
        {text.slice(m.start, m.end)}
      </mark>,
    );
    pos = m.end;
  }
  if (pos < text.length) nodes.push(...renderPlain(text.slice(pos), query, `p${key++}`));
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
  const [query, setQuery] = useState("");
  const [matchIdx, setMatchIdx] = useState(0);
  const [outlineOpen, setOutlineOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

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

  const outline = useMemo(() => {
    const ordOf = new Map(entries.map((e) => [e.entry_id, e.ordinal]));
    const figFirst = new Map<string, number>();
    for (const f of artifact?.figure_mentions ?? []) {
      const ord = ordOf.get(f.entry_id);
      if (ord === undefined) continue;
      for (const fid of f.figure_ids) {
        const prev = figFirst.get(fid);
        if (prev === undefined || ord < prev) figFirst.set(fid, ord);
      }
    }
    return detectOutline(entries, figFirst);
  }, [entries, artifact]);

  const scrollToOrdinal = useCallback((ordinal: number) => {
    document.getElementById(`spec-L${ordinal}`)?.scrollIntoView({ block: "center" });
  }, []);

  const selectRange = useCallback(
    (start: number, end: number, opts: { scroll?: boolean } = {}) => {
      setSelection({ start, end });
      const e = entries.find((x) => x.ordinal === start);
      if (e) setPdfPage(e.page_index + 1);
      if (opts.scroll) scrollToOrdinal(start);
      history.replaceState(null, "", buildViewHash(documentId, start, end));
    },
    [entries, scrollToOrdinal, documentId],
  );

  const selectLine = useCallback((e: EntryDto) => selectRange(e.ordinal, e.ordinal), [selectRange]);

  // Deep link: honor #L<ordinal>[-<end>] once entries are loaded.
  const deepLinked = useRef(false);
  useEffect(() => {
    if (deepLinked.current || entries.length === 0) return;
    deepLinked.current = true;
    const target = parseViewHash(window.location.hash);
    if (target.start !== undefined && entries.some((e) => e.ordinal === target.start)) {
      selectRange(target.start, target.end ?? target.start, { scroll: true });
    }
  }, [entries, selectRange]);

  const matches = useMemo(() => searchEntries(entries, query), [entries, query]);
  useEffect(() => setMatchIdx(0), [query]);

  const gotoMatch = useCallback(
    (idx: number) => {
      if (matches.length === 0) return;
      const wrapped = ((idx % matches.length) + matches.length) % matches.length;
      setMatchIdx(wrapped);
      const ord = matches[wrapped];
      selectRange(ord, ord, { scroll: true });
    },
    [matches, selectRange],
  );

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

  // Keyboard: '/' focuses search; arrows move the selected line; Esc clears search.
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      const inField = ev.target instanceof HTMLInputElement || ev.target instanceof HTMLTextAreaElement;
      if (ev.key === "/" && !inField) {
        ev.preventDefault();
        searchRef.current?.focus();
      } else if (ev.key === "Escape" && inField) {
        setQuery("");
        (ev.target as HTMLInputElement).blur();
      } else if ((ev.key === "ArrowDown" || ev.key === "ArrowUp") && !inField && selection) {
        ev.preventDefault();
        const step = ev.key === "ArrowDown" ? 1 : -1;
        const next = Math.max(0, Math.min(entries.length - 1, selection.start + step));
        const e = entries[next];
        if (e) selectRange(e.ordinal, e.ordinal, { scroll: true });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [entries, selection, selectRange]);

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

        {artifact && showText && (
          <>
            <button
              type="button"
              className={`secondary outline-toggle${outlineOpen ? " active" : ""}`}
              aria-pressed={outlineOpen}
              onClick={() => setOutlineOpen((v) => !v)}
              title="Toggle outline"
            >
              ☰ Outline
            </button>
            <div className="search" role="search">
              <input
                ref={searchRef}
                type="search"
                placeholder="Search text  (/)"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") gotoMatch(matchIdx + (e.shiftKey ? -1 : 1));
                }}
                aria-label="Search specification text"
              />
              {query && (
                <>
                  <span className="search-count">
                    {matches.length ? `${matchIdx + 1} / ${matches.length}` : "0"}
                  </span>
                  <button
                    type="button"
                    className="secondary"
                    disabled={!matches.length}
                    onClick={() => gotoMatch(matchIdx - 1)}
                    aria-label="Previous match"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={!matches.length}
                    onClick={() => gotoMatch(matchIdx + 1)}
                    aria-label="Next match"
                  >
                    ↓
                  </button>
                </>
              )}
            </div>
          </>
        )}

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
          {showText && outlineOpen && (
            <nav className="outline" aria-label="Outline">
              {outline.length === 0 ? (
                <p className="muted small">No sections detected.</p>
              ) : (
                outline.map((item) => (
                  <button
                    key={`${item.kind}-${item.ordinal}-${item.label}`}
                    type="button"
                    className={`outline-item ${item.kind}`}
                    onClick={() => selectRange(item.ordinal, item.ordinal, { scroll: true })}
                  >
                    <span className="outline-label">{item.label}</span>
                    <span className="outline-ref">{item.ref}</span>
                  </button>
                ))
              )}
            </nav>
          )}
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
                    id={`spec-L${e.ordinal}`}
                    className={`spec-line${sel ? " selected" : ""}`}
                    onClick={() => selectLine(e)}
                  >
                    <span className={`conf ${e.text_confidence}`} title={e.text_confidence} />
                    <span className="ref">{refShort(e.locator)}</span>
                    <span className="line-text">{renderText(e, marks, query)}</span>
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
              onSelectRange={(a, b) => selectRange(a, b)}
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
            <button
              type="button"
              className="secondary"
              onClick={() => copy("link", window.location.href)}
              title="Copy a deep link to this line"
            >
              Copy link
            </button>
            {copied && <span className="copied">Copied {copied}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
