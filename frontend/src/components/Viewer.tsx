import {
  type ClipboardEvent as ReactClipboardEvent,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

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
  FigureOccurrenceDto,
  NumeralMentionDto,
  OverrideRead,
  PatentFrontMatterDto,
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
  buildFigureOutline,
  buildViewHash,
  detectClaims,
  detectOutline,
  highlightSegments,
  parseViewHash,
  refShort,
  resolveFigurePage,
  scopedEntries,
  searchEntries,
} from "../spec/navigation";
import { exportPortable, exportText } from "../spec/export";
import { detectDisplayFigureMentions } from "../spec/figures";
import {
  deriveIndentLevels,
  deriveParagraphStarts,
  formatSelectionWithCitation,
  joinEntryText,
  normalizeCopiedText,
} from "../spec/text";
import { PdfPane } from "./PdfPane";
import { Icon, type IconName } from "./Icon";

type Layout = "text" | "pdf" | "split" | "details";
type Selection = { start: number; end: number } | null;
type OutlineSectionKey = "figures" | "specification" | "bookmarks" | "notes" | "claims";
const VIEWER_PREFERENCES_KEY = "xray.viewer.preferences.v1";
// Keep the dormant callout code and artifact fields available for a later,
// measured reintroduction, but do not expose unreliable numeral associations.
const CALLOUT_IDENTIFICATION_ENABLED = false;

function loadViewerPreferences(): {
  layout: Layout;
  splitPercent: number;
  outlineOpen: boolean;
} {
  try {
    const saved = JSON.parse(localStorage.getItem(VIEWER_PREFERENCES_KEY) ?? "{}");
    const layout = ["text", "pdf", "split", "details"].includes(saved.layout)
      ? (saved.layout as Layout)
      : "text";
    const splitPercent = Number.isFinite(saved.splitPercent)
      ? Math.max(24, Math.min(76, saved.splitPercent))
      : 42;
    return { layout, splitPercent, outlineOpen: saved.outlineOpen === true };
  } catch {
    return { layout: "text", splitPercent: 42, outlineOpen: false };
  }
}

type Mark = {
  start: number;
  end: number;
  cls: string;
  title: string;
  mention?: NumeralMentionDto;
  figure?: FigureMentionDto;
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
      title: `Figure reference → ${f.figure_ids.join(", ")} (click to view)`,
      figure: f,
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
  const len = entry.display_text.length;
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
  onFigureClick: (m: FigureMentionDto) => void,
): ReactNode {
  const text = entry.display_text;
  const nodes: ReactNode[] = [];
  let pos = 0;
  let key = 0;
  for (const m of marks) {
    if (m.start < pos) continue;
    if (m.start > pos) nodes.push(...renderPlain(text.slice(pos, m.start), query, `p${key++}`));
    const mention = m.mention;
    const figure = m.figure;
    const clickable = !!mention || !!figure;
    const activate = () => {
      if (mention) onMentionClick(mention);
      else if (figure) onFigureClick(figure);
    };
    nodes.push(
      <mark
        key={`m${key++}`}
        className={clickable ? `${m.cls} clickable` : m.cls}
        title={m.title}
        role={clickable ? "button" : undefined}
        tabIndex={clickable ? 0 : undefined}
        aria-label={clickable ? m.title : undefined}
        onClick={
          clickable
            ? (e) => {
                e.stopPropagation();
                activate();
              }
            : undefined
        }
        onKeyDown={
          clickable
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  e.stopPropagation();
                  activate();
                }
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

function copiedTextFromRange(range: Range): string {
  const fragment = range.cloneContents();
  const copySurface = document.createElement("div");
  copySurface.appendChild(fragment);
  copySurface.querySelectorAll("[data-copy-exclude]").forEach((node) => node.remove());
  const selectedRows = Array.from(copySurface.querySelectorAll<HTMLElement>(".spec-line"));
  const rowText = selectedRows
    .map((row) => ({
      paragraphStart: row.classList.contains("paragraph-start"),
      text: row.querySelector<HTMLElement>(".line-text")?.textContent?.trimEnd() ?? "",
    }))
    .filter((row) => row.text.trim().length > 0);
  const selectedLines = Array.from(copySurface.querySelectorAll<HTMLElement>(".line-text"))
    .map((line) => line.textContent?.trimEnd() ?? "")
    .filter((line) => line.trim().length > 0);
  const raw = rowText.length > 0
    ? rowText.reduce(
        (text, row, index) =>
          `${text}${index > 0 ? (row.paragraphStart ? "\n\n" : "\n") : ""}${row.text}`,
        "",
      )
    : selectedLines.length > 0
      ? selectedLines.join("\n")
      : copySurface.textContent ?? "";
  return normalizeCopiedText(raw);
}

function frontMatterParagraphs(value: string | null | undefined): string[] {
  return (value ?? "")
    .split(/\r?\n\s*\r?\n/)
    .map((paragraph) => paragraph.replace(/\s*\r?\n\s*/g, " ").trim())
    .filter(Boolean);
}

function PatentFrontMatter({
  value,
  pdfVisible,
  onNavigate,
}: {
  value: PatentFrontMatterDto;
  pdfVisible: boolean;
  onNavigate: () => void;
}) {
  const paragraphs = frontMatterParagraphs(value.abstract);
  const activate = () => {
    const nativeSelection = window.getSelection();
    if (!nativeSelection || nativeSelection.isCollapsed) onNavigate();
  };
  const keyboardActivate = (event: ReactKeyboardEvent) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onNavigate();
  };
  const navigationProps = pdfVisible
    ? {
        role: "button",
        tabIndex: 0,
        title: "Go to the first page in the PDF",
        onClick: activate,
        onKeyDown: keyboardActivate,
      }
    : {};

  return (
    <section className="patent-preface" aria-label="Patent information">
      {(value.patent_number || value.title) && (
        <div
          className={`patent-preface-row identity${pdfVisible ? " navigable" : ""}`}
          {...navigationProps}
        >
          {value.patent_number && <p className="patent-preface-number">{value.patent_number}</p>}
          {value.title && <h2>{value.title}</h2>}
        </div>
      )}
      {(value.metadata ?? []).length > 0 && (
        <dl className="patent-preface-metadata">
          {(value.metadata ?? []).map((item, index) => (
            <div
              key={`${item.label}-${index}`}
              className={`patent-preface-row${pdfVisible ? " navigable" : ""}`}
              {...navigationProps}
            >
              <dt>{item.label}</dt>{" "}
              <dd>{item.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {paragraphs.length > 0 && (
        <div className="patent-preface-abstract">
          <h3>Abstract</h3>
          {paragraphs.map((paragraph, index) => (
            <p
              key={index}
              className={`patent-preface-row${pdfVisible ? " navigable" : ""}`}
              {...navigationProps}
            >
              {paragraph}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

export function Viewer({ documentId }: { documentId: string }) {
  const [doc, setDoc] = useState<DocumentRead | null>(null);
  const [artifact, setArtifact] = useState<ArtifactEntries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<Layout>(() => loadViewerPreferences().layout);
  const [selection, setSelection] = useState<Selection>(null);
  const [selectionSource, setSelectionSource] = useState<"text" | "pdf">("text");
  const [textSelection, setTextSelection] = useState("");
  const [pdfSelection, setPdfSelection] = useState("");
  const [splitPercent, setSplitPercent] = useState(
    () => loadViewerPreferences().splitPercent,
  );
  const [pdfPage, setPdfPage] = useState(1);
  const [copied, setCopied] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<SearchScope>("all");
  const [matchIdx, setMatchIdx] = useState(0);
  const [outlineOpen, setOutlineOpen] = useState(
    () => loadViewerPreferences().outlineOpen,
  );
  const [collapsedOutlineSections, setCollapsedOutlineSections] = useState<
    Set<OutlineSectionKey>
  >(() => new Set());
  const [bookmarks, setBookmarks] = useState<BookmarkRead[]>([]);
  const [annotations, setAnnotations] = useState<AnnotationRead[]>([]);
  const [overrides, setOverrides] = useState<OverrideRead[]>([]);
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteDraft, setNoteDraft] = useState("");
  const [editingAnnotationId, setEditingAnnotationId] = useState<string | null>(null);
  const [citeStyle, setCiteStyle] = useState<CiteStyle>(loadStyle);
  const [citeSettingsOpen, setCiteSettingsOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const panesRef = useRef<HTMLDivElement>(null);
  const specRef = useRef<HTMLElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(
        VIEWER_PREFERENCES_KEY,
        JSON.stringify({ layout, splitPercent, outlineOpen }),
      );
    } catch {
      // Private browsing or storage policy can disable persistence.
    }
  }, [layout, splitPercent, outlineOpen]);

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
        const [bm, an, ov] = await Promise.all([
          api.listBookmarks(documentId),
          api.listAnnotations(documentId),
          CALLOUT_IDENTIFICATION_ENABLED
            ? api.listOverrides(documentId)
            : Promise.resolve([] as OverrideRead[]),
        ]);
        if (!cancelled) {
          setBookmarks(bm);
          setAnnotations(an);
          setOverrides(ov);
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
  const indentLevels = useMemo(() => deriveIndentLevels(entries), [entries]);
  const paragraphStarts = useMemo(() => deriveParagraphStarts(entries), [entries]);
  const figureMentions = useMemo(() => detectDisplayFigureMentions(entries), [entries]);
  const figsByEntry = useMemo(() => groupByEntry(figureMentions), [figureMentions]);
  const numsByEntry = useMemo(
    () => groupByEntry(CALLOUT_IDENTIFICATION_ENABLED ? artifact?.numeral_mentions ?? [] : []),
    [artifact],
  );
  const assocByKey = useMemo(() => {
    const map = new Map<string, AssociationDto>();
    for (const a of artifact?.mention_associations ?? []) {
      map.set(`${a.entry_id}:${a.span[0]}:${a.span[1]}`, a);
    }
    return map;
  }, [artifact]);

  const outline = useMemo(() => detectOutline(entries), [entries]);
  const outlineFigures = useMemo(
    () => buildFigureOutline(figureMentions, artifact?.figure_occurrences ?? []),
    [artifact?.figure_occurrences, figureMentions],
  );
  const claims = useMemo(() => detectClaims(entries), [entries]);

  const toggleOutlineSection = useCallback((section: OutlineSectionKey) => {
    setCollapsedOutlineSections((current) => {
      const next = new Set(current);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  }, []);

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

  const beginEditAnnotation = useCallback((annotation: AnnotationRead) => {
    setEditingAnnotationId(annotation.id);
    setNoteDraft(annotation.note);
    setNoteOpen(true);
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

  const selectPdfLine = useCallback(
    (entry: EntryDto) => {
      // A PDF-only layout has no text target to scroll yet, so reveal the text
      // pane first and scroll after React has committed the split layout.
      setLayout((current) => (current === "pdf" ? "split" : current));
      setSelectionSource("pdf");
      selectRange(entry.ordinal, entry.ordinal);
      window.requestAnimationFrame(() => scrollToOrdinal(entry.ordinal));
    },
    [scrollToOrdinal, selectRange],
  );

  // Keyboard within the spec listbox: arrows/Home/End move (and reveal) the
  // selected line, working from no selection too. preventDefault dedupes against
  // the global arrow handler (which serves the click-then-arrow case).
  const onSpecKey = useCallback(
    (ev: ReactKeyboardEvent) => {
      const n = entries.length;
      if (!n) return;
      const cur = selection ? selection.start : -1;
      let target: number | null = null;
      if (ev.key === "ArrowDown") target = Math.min(n - 1, cur + 1);
      else if (ev.key === "ArrowUp") target = cur <= 0 ? 0 : cur - 1;
      else if (ev.key === "Home") target = 0;
      else if (ev.key === "End") target = n - 1;
      else return;
      ev.preventDefault();
      const e = entries[target];
      if (e) selectRange(e.ordinal, e.ordinal, { scroll: true });
    },
    [entries, selection, selectRange],
  );

  // --- figure/callout cross-navigation ---
  const [highlightCallouts, setHighlightCallouts] = useState<Set<string>>(new Set());
  const [focusedFigure, setFocusedFigure] = useState<FigureOccurrenceDto | null>(null);
  const [chooser, setChooser] = useState<{
    mention: NumeralMentionDto;
    candidates: CalloutDto[];
  } | null>(null);
  const mentionCycle = useRef<{ value: string; idx: number } | null>(null);

  const calloutById = useMemo(
    () => new Map((artifact?.callout_occurrences ?? []).map((c) => [c.callout_id, c])),
    [artifact],
  );
  const overrideByKey = useMemo(
    () => new Map(overrides.map((o) => [`${o.entry_id}:${o.span_start}:${o.span_end}`, o])),
    [overrides],
  );

  const persistOverride = useCallback(
    async (mention: NumeralMentionDto, calloutId: string | null) => {
      try {
        const saved = await api.upsertOverride(
          documentId, mention.entry_id, mention.span[0], mention.span[1], calloutId,
        );
        const key = (o: OverrideRead) => `${o.entry_id}:${o.span_start}:${o.span_end}`;
        setOverrides((os) => [...os.filter((o) => key(o) !== key(saved)), saved]);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [documentId],
  );

  const navigateToCallout = useCallback((callout: CalloutDto, highlightIds: string[]) => {
    setLayout((current) =>
      current === "text" ? "split" : current === "details" ? "pdf" : current,
    );
    setSelectionSource("pdf");
    setPdfPage(callout.page_index + 1);
    setFocusedFigure(null);
    setHighlightCallouts(new Set(highlightIds));
  }, []);

  const navigateToFigureIds = useCallback(
    (figureIds: string[]) => {
      const normalize = (value: string) =>
        value.replace(/^fig(?:ure)?\.?\s*/i, "").replace(/\s+/g, "").toUpperCase();
      const wanted = new Set(figureIds.map(normalize));
      const occurrences = artifact?.figure_occurrences ?? [];
      const figures = occurrences
        .filter((figure) => wanted.has(normalize(figure.figure_id)))
        .sort((a, b) => a.page_index - b.page_index);
      const targetPage = resolveFigurePage(figureIds, occurrences);
      if (targetPage === null) return;
      setLayout((current) =>
        current === "text" ? "split" : current === "details" ? "pdf" : current,
      );
      setSelectionSource("pdf");
      setPdfPage(targetPage + 1);
      setFocusedFigure(figures[0] ? { ...figures[0] } : null);
      setHighlightCallouts(new Set());
    },
    [artifact],
  );
  const navigateToFigure = useCallback(
    (mention: FigureMentionDto) => navigateToFigureIds(mention.figure_ids),
    [navigateToFigureIds],
  );

  const navigateToCoverSheet = useCallback(() => {
    setLayout((current) =>
      current === "text" ? "split" : current === "details" ? "pdf" : current,
    );
    setSelectionSource("pdf");
    setPdfPage(1);
    setFocusedFigure(null);
    setHighlightCallouts(new Set());
  }, []);

  // Forward: text numeral → its drawing callout(s); ambiguous opens a chooser.
  const onMentionClick = useCallback(
    (mention: NumeralMentionDto) => {
      const key = `${mention.entry_id}:${mention.span[0]}:${mention.span[1]}`;
      // A saved override wins over the engine's association.
      const ov = overrideByKey.get(key);
      if (ov) {
        const c = ov.callout_id ? calloutById.get(ov.callout_id) : undefined;
        if (c) navigateToCallout(c, [c.callout_id]);
        setChooser(null);
        return;
      }
      const a = assocByKey.get(key);
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
    [overrideByKey, assocByKey, calloutById, navigateToCallout],
  );

  // Reverse: drawing callout → cycle through the text mentions of that numeral.
  const onSelectCallout = useCallback(
    (c: CalloutDto) => {
      const ms = (artifact?.numeral_mentions ?? []).filter((m) => m.value === c.value);
      if (ms.length === 0) return;
      const cur = mentionCycle.current;
      const idx = cur && cur.value === c.value ? (cur.idx + 1) % ms.length : 0;
      mentionCycle.current = { value: c.value, idx };
      setLayout((current) =>
        current === "pdf" ? "split" : current === "details" ? "text" : current,
      );
      setFocusedFigure(null);
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
    () => new Set(figureMentions.map((f) => f.entry_id)),
    [figureMentions],
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
      if (ev.defaultPrevented) return; // already handled (e.g. by the spec listbox)
      const inField = ev.target instanceof HTMLInputElement || ev.target instanceof HTMLTextAreaElement;
      if (ev.key === "Escape" && chooser) {
        ev.preventDefault();
        setChooser(null);
      } else if (ev.key === "/" && !inField) {
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
  }, [entries, selection, selectRange, chooser]);

  const selectedStartEntry = selection
    ? (entries.find((e) => e.ordinal === selection.start) ?? null)
    : null;

  const saveNote = useCallback(async () => {
    const text = noteDraft.trim();
    if (!text || (!editingAnnotationId && !selectedStartEntry)) return;
    try {
      if (editingAnnotationId) {
        const updated = await api.updateAnnotation(editingAnnotationId, text);
        setAnnotations((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      } else if (selectedStartEntry) {
        const created = await api.createAnnotation(documentId, selectedStartEntry.entry_id, text);
        setAnnotations((items) => [created, ...items]);
      }
      setNoteDraft("");
      setNoteOpen(false);
      setEditingAnnotationId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [noteDraft, selectedStartEntry, documentId, editingAnnotationId]);

  const citation =
    selectedEntries.length && doc
      ? formatCitation(selectedEntries, doc.patent_number ?? doc.title, citeStyle)
      : "";
  const selectedText = joinEntryText(selectedEntries);
  const surfaceSelection = selectionSource === "pdf" ? pdfSelection : textSelection;
  const activeSelectionText = normalizeCopiedText(
    surfaceSelection.trim() ? surfaceSelection : selectedText,
  );
  const highlightOrdinal = selection ? selection.start : null;
  const selectedBookmarked = selectedStartEntry
    ? bookmarkByEntry.has(selectedStartEntry.entry_id)
    : false;

  const showText = layout === "text" || layout === "split";
  const showPdf = layout === "pdf" || layout === "split";
  const paneButtons = [
    { id: "text" as const, label: "Text", icon: "text" as IconName },
    { id: "pdf" as const, label: "PDF", icon: "file" as IconName },
  ];

  function togglePane(pane: "text" | "pdf") {
    setLayout((current) => {
      if (current === "details") return pane;
      if (pane === "text") {
        if (current === "text") return current;
        return current === "pdf" ? "split" : "pdf";
      }
      if (current === "pdf") return current;
      return current === "text" ? "split" : "text";
    });
    setSelectionSource(pane);
  }
  const correctedCount = entries.filter((e) => e.display_text !== e.source_text).length;
  const hasWarnings =
    (artifact?.warnings.length ?? 0) > 0 || artifact?.disposition === "partial";

  const handleTextMouseUp = useCallback(() => {
    const nativeSelection = window.getSelection();
    const container = specRef.current;
    if (!nativeSelection || nativeSelection.isCollapsed || !container) return;
    const range = nativeSelection.getRangeAt(0);
    if (!container.contains(range.commonAncestorContainer)) return;
    const text = copiedTextFromRange(range);
    if (!text.trim()) return;
    setSelectionSource("text");
    setTextSelection(text);
    const coversFrontMatter = Array.from(
      container.querySelectorAll<HTMLElement>(".patent-preface-row"),
    ).some((row) => range.intersectsNode(row));
    const covered = Array.from(container.querySelectorAll<HTMLElement>(".spec-line"))
      .filter((line) => range.intersectsNode(line))
      .map((line) => Number(line.dataset.ordinal))
      .filter((ordinal) => !Number.isNaN(ordinal));
    if (covered.length > 0) selectRange(Math.min(...covered), Math.max(...covered));
    else if (coversFrontMatter) setSelection(null);
  }, [selectRange]);

  const handleTextCopy = useCallback((event: ReactClipboardEvent<HTMLElement>) => {
    const nativeSelection = window.getSelection();
    const container = specRef.current;
    if (!nativeSelection || nativeSelection.isCollapsed || !container) return;
    const range = nativeSelection.getRangeAt(0);
    if (!container.contains(range.commonAncestorContainer)) return;
    const text = copiedTextFromRange(range);
    if (!text.trim()) return;
    const coversSpecification = Array.from(
      container.querySelectorAll<HTMLElement>(".spec-line"),
    ).some((line) => range.intersectsNode(line));
    event.preventDefault();
    event.clipboardData.setData(
      "text/plain",
      coversSpecification ? formatSelectionWithCitation(text, citation) : text,
    );
    setSelectionSource("text");
    setTextSelection(text);
  }, [citation]);

  const navigateFrontMatterToPdf = useCallback(() => {
    if (!showPdf) return;
    setSelection(null);
    setFocusedFigure(null);
    setHighlightCallouts(new Set());
    setPdfPage(1);
  }, [showPdf]);

  const resizeSplit = useCallback((clientX: number) => {
    const container = panesRef.current;
    const textPane = specRef.current;
    const pdfPane = container?.querySelector<HTMLElement>(".pdf-pane");
    if (!textPane || !pdfPane) return;
    const left = textPane.getBoundingClientRect().left;
    const right = pdfPane.getBoundingClientRect().right;
    if (right <= left) return;
    setSplitPercent(Math.max(24, Math.min(76, ((clientX - left) / (right - left)) * 100)));
  }, []);

  const patentNumber = doc?.patent_number?.trim();
  const patentTitle = doc?.title.trim();
  const viewerTitle = patentNumber
    ? patentTitle && patentTitle.replace(/[^a-z0-9]/gi, "").toLowerCase() !==
        patentNumber.replace(/[^a-z0-9]/gi, "").toLowerCase()
      ? `${patentNumber} — ${patentTitle}`
      : patentNumber
    : patentTitle || "…";

  return (
    <div className="viewer">
      <div className="viewer-bar">
        <strong className="viewer-title">{viewerTitle}</strong>

        {artifact && (
          <div className="viewer-mode-nav" aria-label="Viewer navigation">
            <button
              type="button"
              className={`tab outline-toggle${outlineOpen && showText ? " active" : ""}`}
              aria-pressed={outlineOpen && showText}
              onClick={() => {
                if (!showText) {
                  setLayout((current) => current === "pdf" ? "split" : "text");
                }
                setOutlineOpen((value) => !value);
              }}
              title="Toggle outline"
              aria-label="Toggle outline"
            >
              <Icon name="panel-left" size={17} className="viewer-tab-icon" />
            </button>
            <div className="tabs viewer-pane-controls" role="group" aria-label="Visible panes">
              {paneButtons.map((pane) => {
                const active = pane.id === "text" ? showText : showPdf;
                return (
                  <button
                    key={pane.id}
                    type="button"
                    aria-pressed={active}
                    aria-label={
                      active && layout === "split"
                        ? `Hide ${pane.label.toLowerCase()} pane`
                        : active
                          ? `${pane.label} pane is visible`
                          : `Show ${pane.label.toLowerCase()} pane`
                    }
                    className={`tab${active ? " active" : ""}`}
                    onClick={() => togglePane(pane.id)}
                    title={
                      active && layout === "split"
                        ? `Hide ${pane.label.toLowerCase()} pane`
                        : active
                          ? `${pane.label} pane is visible`
                          : `Show ${pane.label.toLowerCase()} pane`
                    }
                  >
                    <Icon name={pane.icon} size={17} className="viewer-tab-icon" />
                  </button>
                );
              })}
              <button
                type="button"
                aria-pressed={layout === "details"}
                className={`tab${layout === "details" ? " active" : ""}`}
                onClick={() => setLayout((current) => current === "details" ? "text" : "details")}
                aria-label="Toggle document details"
                title="Document details"
              >
                <Icon name="circle-info" size={17} className="viewer-tab-icon" />
              </button>
            </div>
          </div>
        )}

        {artifact && showText && (
          <>
            <div className="search" role="search">
              <Icon name="search" size={16} className="search-leading-icon" />
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
                    <Icon name="chevron-up" size={16} />
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={!matches.length}
                    onClick={() => gotoMatch(matchIdx + 1)}
                    aria-label="Next match"
                  >
                    <Icon name="chevron-down" size={16} />
                  </button>
                </>
              )}
            </div>
          </>
        )}

        {artifact && (
          <div className="export-menu">
            <button
              type="button"
              className="icon-button"
              aria-haspopup="true"
              aria-label="Export patent"
              title="Export patent"
            >
              <Icon name="download" size={17} />
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

      {artifact && hasWarnings && (
        <div className="quality-banner" role="status">
          <strong>
            {artifact.disposition === "partial" ? "Partial extraction" : "Extracted with warnings"}
          </strong>
          {artifact.warnings.length > 0 && <span> — {artifact.warnings.join("; ")}</span>}
        </div>
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
            <dd>{figureMentions.length}</dd>
            {CALLOUT_IDENTIFICATION_ENABLED && (
              <>
                <dt>Reference numerals</dt>
                <dd>{artifact.numeral_mentions.length}</dd>
                <dt>Drawing callouts</dt>
                <dd>{artifact.callout_occurrences.length}</dd>
              </>
            )}
            <dt>Alignment-corrected lines</dt>
            <dd>
              {correctedCount}
              {entries.length > 0 && ` (${Math.round((correctedCount / entries.length) * 100)}%)`}
            </dd>
          </dl>
          {artifact.warnings.length > 0 && (
            <>
              <h3 className="quality-head">Warnings</h3>
              <ul className="warnings">
                {artifact.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {artifact && layout !== "details" && (
        <div
          ref={panesRef}
          className={`panes ${layout}`}
          style={{ "--split-percent": `${splitPercent}%` } as CSSProperties}
        >
          {showText && outlineOpen && (
            <nav className="outline" aria-label="Outline">
              <button
                type="button"
                className="outline-item outline-cover-sheet"
                onClick={navigateToCoverSheet}
              >
                <span className="outline-label">Cover Sheet</span>
                <span className="outline-ref">Page 1</span>
              </button>
              <section className="outline-section">
                <h3 className="outline-head">
                  <button
                    type="button"
                    className="outline-section-toggle"
                    aria-expanded={!collapsedOutlineSections.has("figures")}
                    aria-controls="outline-figures"
                    onClick={() => toggleOutlineSection("figures")}
                  >
                    <span className="outline-chevron" aria-hidden="true">▾</span>
                    <span>Figures ({outlineFigures.length})</span>
                  </button>
                </h3>
                <div
                  id="outline-figures"
                  className="outline-section-content"
                  hidden={collapsedOutlineSections.has("figures")}
                >
                  {outlineFigures.map((item) => (
                    <button
                      key={item.figureId}
                      type="button"
                      className="outline-item"
                      onClick={() => navigateToFigureIds([item.figureId])}
                    >
                      <span className="outline-label">{item.label}</span>
                      <span className="outline-ref">Page {item.page}</span>
                    </button>
                  ))}
                  {outlineFigures.length === 0 && (
                    <p className="muted small outline-empty">No figures detected.</p>
                  )}
                </div>
              </section>
              {outline.length > 0 && (
                <section className="outline-section">
                  <h3 className="outline-head">
                    <button
                      type="button"
                      className="outline-section-toggle"
                      aria-expanded={!collapsedOutlineSections.has("specification")}
                      aria-controls="outline-specification"
                      onClick={() => toggleOutlineSection("specification")}
                    >
                      <span className="outline-chevron" aria-hidden="true">▾</span>
                      <span>Specification</span>
                    </button>
                  </h3>
                  <div
                    id="outline-specification"
                    className="outline-section-content"
                    hidden={collapsedOutlineSections.has("specification")}
                  >
                    {outline.map((item) => (
                    <button
                      key={`${item.kind}-${item.ordinal}-${item.label}`}
                      type="button"
                      className={`outline-item ${item.kind}`}
                      onClick={() => selectRange(item.ordinal, item.ordinal, { scroll: true })}
                    >
                      <span className="outline-label">{item.label}</span>
                      <span className="outline-ref">{item.ref}</span>
                    </button>
                    ))}
                  </div>
                </section>
              )}
              {bookmarks.length > 0 && (
                <section className="outline-section">
                  <h3 className="outline-head">
                    <button
                      type="button"
                      className="outline-section-toggle"
                      aria-expanded={!collapsedOutlineSections.has("bookmarks")}
                      aria-controls="outline-bookmarks"
                      onClick={() => toggleOutlineSection("bookmarks")}
                    >
                      <span className="outline-chevron" aria-hidden="true">▾</span>
                      <span>Bookmarks</span>
                    </button>
                  </h3>
                  <div
                    id="outline-bookmarks"
                    className="outline-section-content"
                    hidden={collapsedOutlineSections.has("bookmarks")}
                  >
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
                  </div>
                </section>
              )}
              {annotations.length > 0 && (
                <section className="outline-section">
                  <h3 className="outline-head">
                    <button
                      type="button"
                      className="outline-section-toggle"
                      aria-expanded={!collapsedOutlineSections.has("notes")}
                      aria-controls="outline-notes"
                      onClick={() => toggleOutlineSection("notes")}
                    >
                      <span className="outline-chevron" aria-hidden="true">▾</span>
                      <span>Notes</span>
                    </button>
                  </h3>
                  <div
                    id="outline-notes"
                    className="outline-section-content"
                    hidden={collapsedOutlineSections.has("notes")}
                  >
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
                          onClick={() => {
                            if (ord !== undefined) selectRange(ord, ord, { scroll: true });
                            beginEditAnnotation(a);
                          }}
                          aria-label="Edit note"
                        >
                          ✎
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
                  </div>
                </section>
              )}
              {claims.length > 0 && (
                <section className="outline-section">
                  <h3 className="outline-head">
                    <button
                      type="button"
                      className="outline-section-toggle"
                      aria-expanded={!collapsedOutlineSections.has("claims")}
                      aria-controls="outline-claims"
                      onClick={() => toggleOutlineSection("claims")}
                    >
                      <span className="outline-chevron" aria-hidden="true">▾</span>
                      <span>Claims ({claims.length})</span>
                    </button>
                  </h3>
                  <div
                    id="outline-claims"
                    className="outline-section-content"
                    hidden={collapsedOutlineSections.has("claims")}
                  >
                    {claims.map((item) => (
                    <button
                      key={`claim-${item.ordinal}`}
                      type="button"
                      className="outline-item claim"
                      onClick={() => selectRange(item.ordinal, item.ordinal, { scroll: true })}
                    >
                      <span className="outline-label">{item.label}</span>
                      <span className="outline-ref">{item.ref}</span>
                    </button>
                    ))}
                  </div>
                </section>
              )}
            </nav>
          )}
          {showText && (
            <section
              ref={specRef}
              className="spec"
              role="listbox"
              tabIndex={0}
              aria-label="Specification text"
              aria-activedescendant={selection ? `spec-L${selection.start}` : undefined}
              onKeyDown={onSpecKey}
              onPointerDown={() => {
                setSelectionSource("text");
                setTextSelection("");
              }}
              onMouseUp={handleTextMouseUp}
              onCopy={handleTextCopy}
            >
              {artifact.front_matter && (
                <PatentFrontMatter
                  value={artifact.front_matter}
                  pdfVisible={showPdf}
                  onNavigate={navigateFrontMatterToPdf}
                />
              )}
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
                const paragraphStart = paragraphStarts.has(e.entry_id);
                const indentLevel = indentLevels.get(e.entry_id) ?? e.indent_level ?? 0;
                return (
                  <div
                    key={e.entry_id}
                    id={`spec-L${e.ordinal}`}
                    data-ordinal={e.ordinal}
                    className={`spec-line${sel ? " selected" : ""}${paragraphStart ? " paragraph-start" : ""}`}
                    role="option"
                    aria-selected={!!sel}
                    onClick={() => {
                      const nativeSelection = window.getSelection();
                      if (!nativeSelection || nativeSelection.isCollapsed) selectLine(e);
                    }}
                  >
                    <span className="spec-gutter" data-copy-exclude="true" aria-hidden="true">
                      <span className="marks">
                        {bookmarkByEntry.has(e.entry_id) && (
                          <span className="mark-bookmark" title="Bookmarked">★</span>
                        )}
                        {noteCount > 0 && (
                          <span className="mark-note" title={`${noteCount} note(s)`}>●</span>
                        )}
                      </span>
                      <span className="ref">{refShort(e.locator)}</span>
                    </span>
                    <span className="line-text">
                      {indentLevel > 0 && (
                        <span className="line-indent">{"\t".repeat(indentLevel)}</span>
                      )}
                      {renderText(e, marks, query, onMentionClick, navigateToFigure)}
                    </span>
                  </div>
                );
              })}
            </section>
          )}
          {showText && showPdf && (
            <div
              className="pane-resizer"
              role="separator"
              aria-label="Resize text and PDF panes"
              aria-orientation="vertical"
              aria-valuemin={24}
              aria-valuemax={76}
              aria-valuenow={Math.round(splitPercent)}
              tabIndex={0}
              onPointerDown={(event) => {
                event.currentTarget.setPointerCapture(event.pointerId);
                resizeSplit(event.clientX);
              }}
              onPointerMove={(event) => {
                if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                  resizeSplit(event.clientX);
                }
              }}
              onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)}
              onKeyDown={(event) => {
                if (event.key === "ArrowLeft") {
                  event.preventDefault();
                  setSplitPercent((value) => Math.max(24, value - 2));
                } else if (event.key === "ArrowRight") {
                  event.preventDefault();
                  setSplitPercent((value) => Math.min(76, value + 2));
                }
              }}
            >
              <span aria-hidden="true" />
            </div>
          )}
          {showPdf && (
            <PdfPane
              documentId={documentId}
              entries={entries}
              callouts={CALLOUT_IDENTIFICATION_ENABLED ? artifact.callout_occurrences : []}
              figures={artifact.figure_occurrences}
              focusedFigure={focusedFigure}
              page={pdfPage}
              onPageChange={setPdfPage}
              highlightOrdinal={highlightOrdinal}
              highlightCallouts={highlightCallouts}
              onSelectLine={selectPdfLine}
              onSelectRange={(a, b) => selectRange(a, b)}
              onSelectCallout={onSelectCallout}
              onActivate={() => {
                setSelectionSource("pdf");
                setPdfSelection("");
              }}
              onSelectionText={(text) => {
                setSelectionSource("pdf");
                setPdfSelection(text);
              }}
              copyCitation={citation}
            />
          )}
        </div>
      )}

      {CALLOUT_IDENTIFICATION_ENABLED && chooser && (
        <div className="chooser" role="dialog" aria-modal="true" aria-label="Choose a callout">
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
            {chooser.candidates.map((c, i) => (
              <button
                key={c.callout_id}
                type="button"
                className="secondary"
                autoFocus={i === 0}
                onClick={() => {
                  navigateToCallout(c, [c.callout_id]);
                  persistOverride(chooser.mention, c.callout_id);
                  setChooser(null);
                }}
              >
                {c.value}
                {c.figure_id ? ` · FIG. ${c.figure_id}` : ""} · p.{c.page_index + 1}
              </button>
            ))}
            <button
              type="button"
              className="secondary"
              onClick={() => {
                persistOverride(chooser.mention, null);
                setChooser(null);
              }}
              title="Record that none of these is correct"
            >
              None of these
            </button>
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
                    onClick={() => beginEditAnnotation(a)}
                    aria-label="Edit note"
                  >
                    <Icon name="pencil" size={15} />
                  </button>
                  <button
                    type="button"
                    className="link-btn"
                    onClick={() => removeAnnotation(a.id)}
                    aria-label="Delete note"
                  >
                    <Icon name="trash" size={15} />
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
                Include short patent name
              </label>
              <label>
                Grant locator
                <select
                  value={citeStyle.grantFormat}
                  onChange={(e) =>
                    updateStyle({ grantFormat: e.target.value as CiteStyle["grantFormat"] })
                  }
                >
                  <option value="colon">3:22</option>
                  <option value="labels">col. 3, l. 22</option>
                </select>
              </label>
              <label>
                Column
                <select
                  value={citeStyle.columnWord}
                  disabled={citeStyle.grantFormat === "colon"}
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
                  disabled={citeStyle.grantFormat === "colon"}
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
                placeholder={editingAnnotationId ? "Edit this note…" : "Add a note for this line…"}
                rows={2}
                autoFocus
              />
              <button type="button" onClick={saveNote} disabled={!noteDraft.trim()}>
                {editingAnnotationId ? "Save changes" : "Save note"}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setNoteOpen(false);
                  setNoteDraft("");
                  setEditingAnnotationId(null);
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
                className={`icon-button${selectedBookmarked ? " active" : ""}`}
                onClick={() => toggleBookmark(selectedStartEntry)}
                title={selectedBookmarked ? "Remove bookmark" : "Bookmark this line"}
                aria-label={selectedBookmarked ? "Remove bookmark" : "Bookmark this line"}
              >
                <Icon name="bookmark" size={17} />
              </button>
            )}
            <button
              type="button"
              className="icon-button"
              onClick={() => {
                setEditingAnnotationId(null);
                setNoteDraft("");
                setNoteOpen((v) => !v);
              }}
              disabled={!selectedStartEntry}
              aria-label="Add note"
              title="Add note"
            >
              <Icon name="message-square" size={17} />
            </button>
            <button
              type="button"
              className={`icon-button${citeSettingsOpen ? " active" : ""}`}
              onClick={() => setCiteSettingsOpen((v) => !v)}
              title="Citation format"
              aria-label="Citation format"
            >
              <Icon name="settings" size={17} />
            </button>
            <button
              type="button"
              className="icon-button"
              onClick={() => copy("selection", activeSelectionText)}
              aria-label="Copy selection"
              title="Copy selection"
            >
              <Icon name="copy" size={17} />
            </button>
            <button
              type="button"
              className="icon-button"
              onClick={() => copy("cite", citation)}
              aria-label="Copy citation"
              title="Copy citation"
            >
              <Icon name="quote" size={17} />
            </button>
            <button
              type="button"
              onClick={() => copy("both", formatSelectionWithCitation(activeSelectionText, citation))}
            >
              Copy selection + citation
            </button>
            <button
              type="button"
              className="icon-button"
              onClick={() => copy("link", window.location.href)}
              title="Copy a deep link to this line"
              aria-label="Copy a deep link to this line"
            >
              <Icon name="link" size={17} />
            </button>
            {copied && <span className="copied">Copied {copied}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
