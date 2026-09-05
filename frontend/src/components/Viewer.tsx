import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import type {
  AnnotationRead,
  ArtifactEntries,
  AssociationDto,
  BookmarkRead,
  CalloutDto,
  DocumentRead,
  EntryDto,
  FigureMentionDto,
  NumeralMentionDto,
} from "../api/types";
import {
  type CiteStyle,
  CITE_PRESETS,
  formatCitation,
  formatRef,
  loadStyle,
  saveStyle,
} from "../spec/citation";
import {
  type SearchScope,
  buildViewHash,
  detectOutline,
  highlightSegments,
  parseViewHash,
  refShort,
  scopedEntries,
  searchEntries,
} from "../spec/navigation";
import { exportPortable, exportText } from "../spec/export";
import { PdfPane } from "./PdfPane";

type Layout = "text" | "pdf" | "split" | "details";
type Selection = { start: number; end: number } | null;

type Mark = {
  start: number;
  end: number;
  cls: string;
  title: string;
  mention?: NumeralMentionDto;
};

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
      title: `Reference numeral ${n.value}${label} — ${status} (click to locate on drawing)`,
      mention: n,
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

function renderText(
  entry: EntryDto,
  marks: Mark[],
  query: string,
  onMentionClick: (m: NumeralMentionDto) => void,
): ReactNode {
  const text = entry.source_text;
  const nodes: ReactNode[] = [];
  let pos = 0;
  let key = 0;
  for (const m of marks) {
    if (m.start < pos) continue;
    if (m.start > pos) nodes.push(...renderPlain(text.slice(pos, m.start), query, `p${key++}`));
    const mention = m.mention;
    nodes.push(
      <mark
        key={`m${key++}`}
        className={mention ? `${m.cls} clickable` : m.cls}
        title={m.title}
        onClick={
          mention
            ? (e) => {
                e.stopPropagation();
                onMentionClick(mention);
              }
            : undefined
        }
      >
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
  const [scope, setScope] = useState<SearchScope>("all");
  const [matchIdx, setMatchIdx] = useState(0);
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [bookmarks, setBookmarks] = useState<BookmarkRead[]>([]);
  const [annotations, setAnnotations] = useState<AnnotationRead[]>([]);
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteDraft, setNoteDraft] = useState("");
  const [citeStyle, setCiteStyle] = useState<CiteStyle>(loadStyle);
  const [citeSettingsOpen, setCiteSettingsOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const updateStyle = useCallback((patch: Partial<CiteStyle>) => {
    setCiteStyle((s) => {
      const next = { ...s, ...patch };
      saveStyle(next);
      return next;
    });
  }, []);

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
        const [bm, an] = await Promise.all([
          api.listBookmarks(documentId),
          api.listAnnotations(documentId),
        ]);
        if (!cancelled) {
          setBookmarks(bm);
          setAnnotations(an);
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

  const ordByEntryId = useMemo(
    () => new Map(entries.map((e) => [e.entry_id, e.ordinal])),
    [entries],
  );
  const bookmarkByEntry = useMemo(
    () => new Map(bookmarks.map((b) => [b.entry_id, b])),
    [bookmarks],
  );
  const notesByEntry = useMemo(() => {
    const m = new Map<string, AnnotationRead[]>();
    for (const a of annotations) {
      const arr = m.get(a.target_entry_id);
      if (arr) arr.push(a);
      else m.set(a.target_entry_id, [a]);
    }
    return m;
  }, [annotations]);

  const toggleBookmark = useCallback(
    async (entry: EntryDto) => {
      const existing = bookmarkByEntry.get(entry.entry_id);
      try {
        if (existing) {
          await api.deleteBookmark(existing.id);
          setBookmarks((bs) => bs.filter((b) => b.id !== existing.id));
        } else {
          const created = await api.createBookmark(documentId, entry.entry_id, refShort(entry.locator));
          setBookmarks((bs) => [created, ...bs]);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [bookmarkByEntry, documentId],
  );

  const removeBookmark = useCallback(async (id: string) => {
    try {
      await api.deleteBookmark(id);
      setBookmarks((bs) => bs.filter((b) => b.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const removeAnnotation = useCallback(async (id: string) => {
    try {
      await api.deleteAnnotation(id);
      setAnnotations((as) => as.filter((a) => a.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

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

  // --- figure/callout cross-navigation ---
  const [highlightCallouts, setHighlightCallouts] = useState<Set<string>>(new Set());
  const [chooser, setChooser] = useState<{
    mention: NumeralMentionDto;
    candidates: CalloutDto[];
  } | null>(null);
  const mentionCycle = useRef<{ value: string; idx: number } | null>(null);

  const calloutById = useMemo(
    () => new Map((artifact?.callout_occurrences ?? []).map((c) => [c.callout_id, c])),
    [artifact],
  );

  const navigateToCallout = useCallback((callout: CalloutDto, highlightIds: string[]) => {
    setLayout((l) => (l === "text" ? "split" : l));
    setPdfPage(callout.page_index + 1);
    setHighlightCallouts(new Set(highlightIds));
  }, []);

  // Forward: text numeral → its drawing callout(s); ambiguous opens a chooser.
  const onMentionClick = useCallback(
    (mention: NumeralMentionDto) => {
      const a = assocByKey.get(`${mention.entry_id}:${mention.span[0]}:${mention.span[1]}`);
      const ids = (a?.selected_callout_ids.length ? a.selected_callout_ids : a?.candidate_callout_ids) ?? [];
      const cos = ids.map((id) => calloutById.get(id)).filter((c): c is CalloutDto => !!c);
      if (cos.length === 0) return; // unresolved — nothing to point at
      if (a?.status === "ambiguous" && cos.length > 1) {
        setChooser({ mention, candidates: cos });
      } else {
        setChooser(null);
        navigateToCallout(cos[0], cos.map((c) => c.callout_id));
      }
    },
    [assocByKey, calloutById, navigateToCallout],
  );

  // Reverse: drawing callout → cycle through the text mentions of that numeral.
  const onSelectCallout = useCallback(
    (c: CalloutDto) => {
      const ms = (artifact?.numeral_mentions ?? []).filter((m) => m.value === c.value);
      if (ms.length === 0) return;
      const cur = mentionCycle.current;
      const idx = cur && cur.value === c.value ? (cur.idx + 1) % ms.length : 0;
      mentionCycle.current = { value: c.value, idx };
      setLayout((l) => (l === "pdf" ? "split" : l));
      setHighlightCallouts(new Set([c.callout_id]));
      const ord = ordByEntryId.get(ms[idx].entry_id);
      if (ord !== undefined) selectRange(ord, ord, { scroll: true });
    },
    [artifact, ordByEntryId, selectRange],
  );

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

  const figureEntryIds = useMemo(
    () => new Set((artifact?.figure_mentions ?? []).map((f) => f.entry_id)),
    [artifact],
  );
  const matches = useMemo(
    () => searchEntries(scopedEntries(entries, scope, figureEntryIds), query),
    [entries, scope, figureEntryIds, query],
  );
  useEffect(() => setMatchIdx(0), [query, scope]);

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

  const selectedStartEntry = selection
    ? (entries.find((e) => e.ordinal === selection.start) ?? null)
    : null;

  const saveNote = useCallback(async () => {
    const text = noteDraft.trim();
    if (!text || !selectedStartEntry) return;
    try {
      const created = await api.createAnnotation(documentId, selectedStartEntry.entry_id, text);
      setAnnotations((as) => [created, ...as]);
      setNoteDraft("");
      setNoteOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [noteDraft, selectedStartEntry, documentId]);

  const citation =
    selectedEntries.length && doc ? formatCitation(selectedEntries, doc.title, citeStyle) : "";
  const selectedText = selectedEntries.map((e) => e.display_text).join(" ");
  const highlightOrdinal = selection ? selection.start : null;
  const selectedBookmarked = selectedStartEntry
    ? bookmarkByEntry.has(selectedStartEntry.entry_id)
    : false;

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
              <select
                className="search-scope"
                value={scope}
                onChange={(e) => setScope(e.target.value as SearchScope)}
                aria-label="Search scope"
                title="Limit search"
              >
                <option value="all">All</option>
                <option value="claims">Claims</option>
                <option value="figures">Figures</option>
              </select>
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

        {artifact && (
          <div className="export-menu">
            <button type="button" className="secondary" aria-haspopup="true">
              Export ▾
            </button>
            <div className="export-pop">
              <button
                type="button"
                onClick={() => doc && exportPortable(doc, artifact, bookmarks, annotations)}
              >
                Portable save (.json)
              </button>
              <button type="button" onClick={() => doc && exportText(doc, artifact)}>
                Plain text (.txt)
              </button>
            </div>
          </div>
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
              {bookmarks.length > 0 && (
                <>
                  <h3 className="outline-head">Bookmarks</h3>
                  {bookmarks.map((b) => {
                    const ord = ordByEntryId.get(b.entry_id);
                    return (
                      <div key={b.id} className="outline-item bookmark">
                        <button
                          type="button"
                          className="outline-jump"
                          disabled={ord === undefined}
                          onClick={() => ord !== undefined && selectRange(ord, ord, { scroll: true })}
                        >
                          <span className="outline-label">★ {b.label ?? b.entry_id}</span>
                        </button>
                        <button
                          type="button"
                          className="link-btn"
                          onClick={() => removeBookmark(b.id)}
                          aria-label="Remove bookmark"
                        >
                          ×
                        </button>
                      </div>
                    );
                  })}
                </>
              )}
              {annotations.length > 0 && (
                <>
                  <h3 className="outline-head">Notes</h3>
                  {annotations.map((a) => {
                    const ord = ordByEntryId.get(a.target_entry_id);
                    return (
                      <div key={a.id} className="outline-item note">
                        <button
                          type="button"
                          className="outline-jump"
                          disabled={ord === undefined}
                          onClick={() => ord !== undefined && selectRange(ord, ord, { scroll: true })}
                          title={a.note}
                        >
                          <span className="outline-label">● {a.note}</span>
                        </button>
                        <button
                          type="button"
                          className="link-btn"
                          onClick={() => removeAnnotation(a.id)}
                          aria-label="Delete note"
                        >
                          ×
                        </button>
                      </div>
                    );
                  })}
                </>
              )}
              {(bookmarks.length > 0 || annotations.length > 0) && outline.length > 0 && (
                <h3 className="outline-head">Sections</h3>
              )}
              {outline.length === 0 ? (
                bookmarks.length === 0 && annotations.length === 0 ? (
                  <p className="muted small">No sections detected.</p>
                ) : null
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
                const noteCount = notesByEntry.get(e.entry_id)?.length ?? 0;
                return (
                  <div
                    key={e.entry_id}
                    id={`spec-L${e.ordinal}`}
                    className={`spec-line${sel ? " selected" : ""}`}
                    onClick={() => selectLine(e)}
                  >
                    <span className={`conf ${e.text_confidence}`} title={e.text_confidence} />
                    <span className="marks" aria-hidden="true">
                      {bookmarkByEntry.has(e.entry_id) && (
                        <span className="mark-bookmark" title="Bookmarked">
                          ★
                        </span>
                      )}
                      {noteCount > 0 && (
                        <span className="mark-note" title={`${noteCount} note(s)`}>
                          ●
                        </span>
                      )}
                    </span>
                    <span className="ref">{refShort(e.locator)}</span>
                    <span className="line-text">
                      {renderText(e, marks, query, onMentionClick)}
                    </span>
                  </div>
                );
              })}
            </section>
          )}
          {showPdf && (
            <PdfPane
              documentId={documentId}
              entries={entries}
              callouts={artifact.callout_occurrences}
              page={pdfPage}
              onPageChange={setPdfPage}
              highlightOrdinal={highlightOrdinal}
              highlightCallouts={highlightCallouts}
              onSelectLine={selectLine}
              onSelectRange={(a, b) => selectRange(a, b)}
              onSelectCallout={onSelectCallout}
            />
          )}
        </div>
      )}

      {chooser && (
        <div className="chooser" role="dialog" aria-label="Choose a callout">
          <div className="chooser-head">
            <strong>Numeral {chooser.mention.value}</strong> appears on more than one drawing —
            choose which:
            <button
              type="button"
              className="link-btn"
              onClick={() => setChooser(null)}
              aria-label="Cancel"
            >
              ×
            </button>
          </div>
          <div className="chooser-options">
            {chooser.candidates.map((c) => (
              <button
                key={c.callout_id}
                type="button"
                className="secondary"
                onClick={() => {
                  navigateToCallout(c, [c.callout_id]);
                  setChooser(null);
                }}
              >
                {c.value}
                {c.figure_id ? ` · FIG. ${c.figure_id}` : ""} · p.{c.page_index + 1}
              </button>
            ))}
          </div>
        </div>
      )}

      {selectedEntries.length > 0 && (
        <div className="cite-bar" role="region" aria-label="Copy and cite">
          <span className="cite-ref">{citation}</span>
          {selectedStartEntry && (notesByEntry.get(selectedStartEntry.entry_id)?.length ?? 0) > 0 && (
            <ul className="cite-notes">
              {notesByEntry.get(selectedStartEntry.entry_id)?.map((a) => (
                <li key={a.id}>
                  <span>{a.note}</span>
                  <button
                    type="button"
                    className="link-btn"
                    onClick={() => removeAnnotation(a.id)}
                    aria-label="Delete note"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          {citeSettingsOpen && (
            <div className="cite-settings">
              <label>
                Preset
                <select
                  onChange={(e) => {
                    const p = CITE_PRESETS.find((x) => x.id === e.target.value);
                    if (p) updateStyle(p.style);
                  }}
                >
                  {CITE_PRESETS.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={citeStyle.includeTitle}
                  onChange={(e) => updateStyle({ includeTitle: e.target.checked })}
                />
                Include title
              </label>
              <label>
                Column
                <select
                  value={citeStyle.columnWord}
                  onChange={(e) => updateStyle({ columnWord: e.target.value as CiteStyle["columnWord"] })}
                >
                  <option value="col.">col.</option>
                  <option value="column">column</option>
                  <option value="c.">c.</option>
                </select>
              </label>
              <label>
                Line
                <select
                  value={citeStyle.lineWord}
                  onChange={(e) => updateStyle({ lineWord: e.target.value as CiteStyle["lineWord"] })}
                >
                  <option value="l.">l. / ll.</option>
                  <option value="line">line / lines</option>
                </select>
              </label>
              <span className="cite-preview">{citation || formatRef(selectedEntries, citeStyle)}</span>
            </div>
          )}
          {noteOpen && (
            <div className="note-editor">
              <textarea
                value={noteDraft}
                onChange={(e) => setNoteDraft(e.target.value)}
                placeholder="Add a note for this line…"
                rows={2}
                autoFocus
              />
              <button type="button" onClick={saveNote} disabled={!noteDraft.trim()}>
                Save note
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setNoteOpen(false);
                  setNoteDraft("");
                }}
              >
                Cancel
              </button>
            </div>
          )}
          <div className="cite-actions">
            {selectedStartEntry && (
              <button
                type="button"
                className={`secondary${selectedBookmarked ? " active" : ""}`}
                onClick={() => toggleBookmark(selectedStartEntry)}
                title={selectedBookmarked ? "Remove bookmark" : "Bookmark this line"}
              >
                {selectedBookmarked ? "★ Bookmarked" : "☆ Bookmark"}
              </button>
            )}
            <button
              type="button"
              className="secondary"
              onClick={() => setNoteOpen((v) => !v)}
              disabled={!selectedStartEntry}
            >
              Add note
            </button>
            <button
              type="button"
              className={`secondary${citeSettingsOpen ? " active" : ""}`}
              onClick={() => setCiteSettingsOpen((v) => !v)}
              title="Citation format"
            >
              ⚙ Format
            </button>
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
