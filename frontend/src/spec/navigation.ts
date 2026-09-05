// Client-side specification navigation: outline extraction, search, and the
// deep-link hash. Pure over the loaded artifact entries (no server round-trips).

import type { EntryDto, Locator } from "../api/types";

export function refShort(loc: Locator): string {
  return loc.kind === "grant" ? `${loc.column}:${loc.printed_line}` : `[${loc.paragraph}]`;
}

export type OutlineItem = {
  ordinal: number;
  label: string;
  ref: string;
  kind: "heading" | "figure" | "claim";
};

// A run of capitalized words (patent section headings are set in caps), 2-8 words,
// plus a few well-known single-word headings.
const HEADING_RE = /^[A-Z][A-Z0-9 ,.'()\-/&]{3,58}$/;
const SINGLE_WORD_HEADINGS = new Set(["ABSTRACT", "CLAIMS", "BACKGROUND", "SUMMARY", "DRAWINGS"]);

function isHeading(text: string): boolean {
  const t = text.trim();
  if (t !== t.toUpperCase()) return false; // must be all-caps
  if (t.replace(/[^A-Za-z]/g, "").length < 3) return false;
  if (!HEADING_RE.test(t)) return false;
  const words = t.split(/\s+/).filter(Boolean).length;
  return words >= 2 ? words <= 8 : SINGLE_WORD_HEADINGS.has(t);
}

/** Section headings (from all-caps lines) plus a jump-to-first entry per figure. */
export function detectOutline(
  entries: EntryDto[],
  figureFirstOrdinal: Map<string, number>,
): OutlineItem[] {
  const items: OutlineItem[] = [];
  for (const e of entries) {
    if (isHeading(e.display_text)) {
      items.push({
        ordinal: e.ordinal,
        label: e.display_text.trim(),
        ref: refShort(e.locator),
        kind: "heading",
      });
    }
  }
  const byOrdinal = new Map(entries.map((e) => [e.ordinal, e]));
  const figures = [...figureFirstOrdinal.entries()].sort((a, b) =>
    a[0].localeCompare(b[0], undefined, { numeric: true }),
  );
  for (const [figId, ordinal] of figures) {
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

const CLAIMS_START = /^(claims?|what is claimed|i claim)\b/i;

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
