// Client-side specification navigation: outline extraction, search, and the
// deep-link hash. Pure over the loaded artifact entries (no server round-trips).

import type { EntryDto, FigureOccurrenceDto, Locator } from "../api/types";

export function refShort(loc: Locator): string {
  return loc.kind === "grant" ? `${loc.column}:${loc.printed_line}` : `[${loc.paragraph}]`;
}

export type OutlineItem = {
  ordinal: number;
  label: string;
  ref: string;
  kind: "heading" | "figure" | "claim";
};

function normalizedFigureId(value: string): string {
  return value.replace(/^fig(?:ure)?\.?\s*/i, "").replace(/\s+/g, "").toUpperCase();
}

/** Resolve a textual figure reference to its zero-based PDF page.
 *
 * Older artifacts can be missing FIG. 1 when the first drawing sheet is the
 * only image-only sheet. If FIG. 2 is present on the immediately following
 * sheet, use that narrow sequence as a safe legacy fallback. New extraction
 * artifacts carry the exact FIG. 1 occurrence and take the normal path.
 */
export function resolveFigurePage(
  figureIds: string[],
  occurrences: FigureOccurrenceDto[],
): number | null {
  const wanted = new Set(figureIds.map(normalizedFigureId));
  const exactPages = occurrences
    .filter((figure) => wanted.has(normalizedFigureId(figure.figure_id)))
    .map((figure) => figure.page_index);
  if (exactPages.length > 0) return Math.min(...exactPages);

  if (wanted.size === 1 && wanted.has("1")) {
    const figureTwoPages = occurrences
      .filter((figure) => normalizedFigureId(figure.figure_id) === "2")
      .map((figure) => figure.page_index);
    if (figureTwoPages.length > 0) {
      const figureTwoPage = Math.min(...figureTwoPages);
      if (figureTwoPage > 0) return figureTwoPage - 1;
    }
  }
  return null;
}

// A run of capitalized words (patent section headings are set in caps), 2-8 words,
// plus a few well-known single-word headings.
const HEADING_RE = /^[A-Z][A-Z0-9 ,.'()\-/&]{3,58}$/;
const SINGLE_WORD_HEADINGS = new Set(["ABSTRACT", "CLAIMS", "BACKGROUND", "SUMMARY", "DRAWINGS"]);
const CLAIMS_START = /^(claims?|what is claimed|i claim)\b/i;
const SPECIFICATION_HEADING = new RegExp(
  `^(?:${[
    "field\\s+of\\s+the\\s+invention",
    "technical\\s+field",
    "background(?:\\s+of\\s+the\\s+invention)?",
    "summary(?:\\s+of\\s+the\\s+invention)?",
    "brief\\s+description\\s+of\\s+the\\s+drawings",
    "description\\s+of\\s+the\\s+drawings",
    "detailed\\s+description",
    "description\\s+of\\s+(?:the\\s+)?(?:preferred\\s+)?embodiments?",
    "cross-reference\\s+to\\s+related\\s+applications?",
    "related\\s+(?:art|applications?)",
    "objects?\\s+of\\s+the\\s+invention",
    "abstract",
  ].join("|")})`,
  "i",
);

function isHeading(text: string): boolean {
  const t = text.trim();
  if (t !== t.toUpperCase()) return false; // must be all-caps
  if (t.replace(/[^A-Za-z]/g, "").length < 3) return false;
  if (!HEADING_RE.test(t)) return false;
  const words = t.split(/\s+/).filter(Boolean).length;
  return words >= 2 ? words <= 8 : SINGLE_WORD_HEADINGS.has(t);
}

function isHeadingFragment(text: string): boolean {
  const value = text.trim();
  return (
    value === value.toUpperCase() &&
    value.replace(/[^A-Za-z]/g, "").length >= 3 &&
    /^[A-Z][A-Z0-9 ,.'()\-/&]{2,80}$/.test(value)
  );
}

function adjacentHeadingLine(left: EntryDto, right: EntryDto): boolean {
  if (left.page_index !== right.page_index || left.locator.kind !== right.locator.kind) return false;
  if (left.locator.kind === "grant") {
    return (
      left.locator.column === right.locator.column &&
      (right.locator.printed_line ?? 0) - (left.locator.printed_line ?? 0) <= 2
    );
  }
  return true;
}

/** Section headings (from all-caps lines) plus a jump-to-first entry per figure. */
export function detectOutline(
  entries: EntryDto[],
  figureFirstOrdinal: Map<string, number>,
): OutlineItem[] {
  const items: OutlineItem[] = [];
  const claimsStart = claimsStartOrdinal(entries);
  const specificationEntries = entries.filter(
    (entry) => claimsStart === null || entry.ordinal < claimsStart,
  );
  for (let index = 0; index < specificationEntries.length; index += 1) {
    const entry = specificationEntries[index];
    if (!isHeadingFragment(entry.display_text)) continue;
    const fragments = [entry.display_text.trim()];
    let end = index;
    while (
      end + 1 < specificationEntries.length &&
      fragments.length < 3 &&
      isHeadingFragment(specificationEntries[end + 1].display_text) &&
      adjacentHeadingLine(specificationEntries[end], specificationEntries[end + 1])
    ) {
      end += 1;
      fragments.push(specificationEntries[end].display_text.trim());
    }
    const label = fragments.join(" ").replace(/\s+/g, " ");
    // Start the outline at the specification itself, excluding the all-caps
    // patent title that precedes FIELD/BACKGROUND on grant cover pages.
    if (SPECIFICATION_HEADING.test(label) || (items.length > 0 && isHeading(label))) {
      items.push({
        ordinal: entry.ordinal,
        label,
        ref: refShort(entry.locator),
        kind: "heading",
      });
    }
    index = end;
  }
  const byOrdinal = new Map(entries.map((e) => [e.ordinal, e]));
  const figures = [...figureFirstOrdinal.entries()].sort((a, b) =>
    a[0].localeCompare(b[0], undefined, { numeric: true }),
  );
  for (const [figId, ordinal] of figures) {
    if (claimsStart !== null && ordinal >= claimsStart) continue;
    const e = byOrdinal.get(ordinal);
    if (e) {
      items.push({ ordinal, label: `FIG. ${figId}`, ref: refShort(e.locator), kind: "figure" });
    }
  }
  return items;
}

/** Ordinals of entries whose display text contains the (case-insensitive) query. */
export function searchEntries(entries: EntryDto[], query: string): number[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return entries.filter((e) => e.display_text.toLowerCase().includes(q)).map((e) => e.ordinal);
}

export type SearchScope = "all" | "claims" | "figures";

/** The ordinal where the claims section begins, or null if none is detected. */
export function claimsStartOrdinal(entries: EntryDto[]): number | null {
  for (const e of entries) {
    if (CLAIMS_START.test(e.display_text.trim())) return e.ordinal;
  }
  return null;
}

const CLAIM_HEAD = /^(\d{1,3})\s*\.\s/;

/** Outline items for each numbered claim (sequentially numbered from the claims
 * section onward, which avoids matching stray "1." in prose). */
export function detectClaims(entries: EntryDto[]): OutlineItem[] {
  const start = claimsStartOrdinal(entries);
  const items: OutlineItem[] = [];
  let last = 0;
  for (const e of entries) {
    if (start !== null && e.ordinal < start) continue;
    const m = CLAIM_HEAD.exec(e.display_text.trim());
    if (m && Number(m[1]) === last + 1) {
      last += 1;
      items.push({ ordinal: e.ordinal, label: `Claim ${last}`, ref: refShort(e.locator), kind: "claim" });
    }
  }
  return items;
}

/** Restrict entries to a search scope. Falls back to all when a scope is empty. */
export function scopedEntries(
  entries: EntryDto[],
  scope: SearchScope,
  figureEntryIds: Set<string>,
): EntryDto[] {
  if (scope === "figures") {
    const sub = entries.filter((e) => figureEntryIds.has(e.entry_id));
    return sub.length ? sub : entries;
  }
  if (scope === "claims") {
    const start = claimsStartOrdinal(entries);
    return start === null ? entries : entries.filter((e) => e.ordinal >= start);
  }
  return entries;
}

/** Split a line's text into segments around case-insensitive matches of `query`. */
export function highlightSegments(text: string, query: string): { text: string; hit: boolean }[] {
  const q = query.trim();
  if (!q) return [{ text, hit: false }];
  const out: { text: string; hit: boolean }[] = [];
  const lower = text.toLowerCase();
  const ql = q.toLowerCase();
  let pos = 0;
  for (let i = lower.indexOf(ql); i >= 0; i = lower.indexOf(ql, pos)) {
    if (i > pos) out.push({ text: text.slice(pos, i), hit: false });
    out.push({ text: text.slice(i, i + q.length), hit: true });
    pos = i + q.length;
  }
  if (pos < text.length) out.push({ text: text.slice(pos), hit: false });
  return out;
}

// --- deep link: #doc=<id>&line=<start>[-<end>] (shareable across a reload) ---

export function parseViewHash(hash: string): {
  documentId?: string;
  start?: number;
  end?: number;
} {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const documentId = params.get("doc") ?? undefined;
  const line = params.get("line");
  const m = line ? /^(\d+)(?:-(\d+))?$/.exec(line) : null;
  if (!m) return { documentId };
  const a = Number(m[1]);
  const b = m[2] ? Number(m[2]) : a;
  return { documentId, start: Math.min(a, b), end: Math.max(a, b) };
}

export function buildViewHash(documentId: string, start?: number, end?: number): string {
  const params = new URLSearchParams({ doc: documentId });
  if (start !== undefined) {
    params.set("line", end !== undefined && end !== start ? `${start}-${end}` : String(start));
  }
  return `#${params.toString()}`;
}
