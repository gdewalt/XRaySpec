// Client-side specification navigation: outline extraction, search, and the
// deep-link hash. Pure over the loaded artifact entries (no server round-trips).

import type {
  EntryDto,
  FigureMentionDto,
  FigureOccurrenceDto,
  Locator,
} from "../api/types";

export function refShort(loc: Locator): string {
  return loc.kind === "grant" ? `${loc.column}:${loc.printed_line}` : `[${loc.paragraph}]`;
}

export type OutlineItem = {
  ordinal: number;
  label: string;
  ref: string;
  kind: "heading" | "claim";
  claimNumber?: number;
};

export type FigureOutlineItem = {
  figureId: string;
  label: string;
  page: number;
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

/** Unique figures with a resolved drawing-sheet destination, in natural order. */
export function buildFigureOutline(
  mentions: FigureMentionDto[],
  occurrences: FigureOccurrenceDto[],
): FigureOutlineItem[] {
  const ids = new Map<string, string>();
  for (const mention of mentions) {
    for (const id of mention.figure_ids) {
      const normalized = normalizedFigureId(id);
      if (normalized) ids.set(normalized, normalized);
    }
  }
  for (const occurrence of occurrences) {
    const normalized = normalizedFigureId(occurrence.figure_id);
    if (normalized) ids.set(normalized, normalized);
  }

  return [...ids.values()]
    .sort((left, right) => left.localeCompare(right, undefined, { numeric: true }))
    .flatMap((figureId) => {
      const pageIndex = resolveFigurePage([figureId], occurrences);
      return pageIndex === null
        ? []
        : [{ figureId, label: `FIG. ${figureId}`, page: pageIndex + 1 }];
    });
}

// A run of capitalized words (patent section headings are set in caps), 2-8 words,
// plus a few well-known single-word headings.
const HEADING_RE = /^[A-Z][A-Z0-9 ,.'()\-/&]{3,58}$/;
const SINGLE_WORD_HEADINGS = new Set(["ABSTRACT", "CLAIMS", "BACKGROUND", "SUMMARY", "DRAWINGS"]);
// Patent grants use several stock introductions for the claims. In
// particular, newer grants commonly say "The invention claimed is:" rather
// than "What is claimed is:".
const CLAIMS_START = /^(?:claims?|what is claimed(?:\s+is)?|the invention claimed is|the invention is claimed as follows|the following is claimed|i claim|we claim)\b/i;
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

/** Specification section headings detected from all-caps lines. */
export function detectOutline(entries: EntryDto[]): OutlineItem[] {
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
  return items;
}

/** Ordinals of entries whose display text contains the (case-insensitive) query. */
export function searchEntries(entries: EntryDto[], query: string): number[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return entries.filter((e) => e.display_text.toLowerCase().includes(q)).map((e) => e.ordinal);
}

export type SearchScope = "all" | "claims" | "figures";

function explicitClaimsStartOrdinal(entries: EntryDto[]): number | null {
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    if (CLAIMS_START.test(entry.display_text.trim())) return entry.ordinal;

    // OCR can wrap the short introduction across two adjacent printed lines,
    // e.g. "The invention" / "claimed is:". Match that without joining text
    // across pages or columns.
    const next = entries[index + 1];
    if (next && adjacentHeadingLine(entry, next)) {
      const joined = `${entry.display_text.trim()} ${next.display_text.trim()}`;
      if (CLAIMS_START.test(joined)) return entry.ordinal;
    }
  }
  return null;
}

// Accept ordinary starts ("1. A method"), compact OCR ("1.A method"), and
// number-only entries where the claim body begins on the following line. Do not
// accept decimals such as "1.5".
const CLAIM_HEAD = /^(\d{1,3})\s*\.(?:\s+|$|(?=[A-Za-z(]))/;

type ClaimCandidate = {
  entry: EntryDto;
  entryIndex: number;
  number: number;
};

function claimCandidate(entry: EntryDto, entryIndex: number): ClaimCandidate | null {
  const match = CLAIM_HEAD.exec(entry.display_text.trim());
  return match ? { entry, entryIndex, number: Number(match[1]) } : null;
}

function claimItem(candidate: ClaimCandidate): OutlineItem {
  return {
    ordinal: candidate.entry.ordinal,
    label: `Claim ${candidate.number}`,
    ref: refShort(candidate.entry.locator),
    kind: "claim",
    claimNumber: candidate.number,
  };
}

function forwardClaims(entries: EntryDto[], start: number): ClaimCandidate[] {
  const claims: ClaimCandidate[] = [];
  let expected = 1;
  entries.forEach((entry, entryIndex) => {
    if (entry.ordinal < start) return;
    const candidate = claimCandidate(entry, entryIndex);
    if (candidate?.number === expected) {
      claims.push(candidate);
      expected += 1;
    }
  });
  return claims;
}

/** Find the strongest exact descending sequence that reaches claim 1.
 *
 * Numbered prose earlier in the specification is rejected unless it forms a
 * complete sequence back to 1. Provider claim numbers, when available, further
 * constrain and rank candidates. Without provider evidence, require at least
 * two claims so an isolated trailing "1." cannot become a claims section.
 */
function backwardClaims(
  entries: EntryDto[],
  providerClaimNumbers: number[] = [],
): ClaimCandidate[] {
  const numbered = entries.flatMap((entry, entryIndex) => {
    const candidate = claimCandidate(entry, entryIndex);
    return candidate ? [candidate] : [];
  });
  const provider = [...new Set(providerClaimNumbers)]
    .filter((number) => Number.isInteger(number) && number > 0)
    .sort((left, right) => left - right);
  const providerSet = new Set(provider);
  const providerMax = provider.at(-1) ?? null;
  let best: ClaimCandidate[] = [];

  for (let tail = numbered.length - 1; tail >= 0; tail -= 1) {
    const reverse = [numbered[tail]];
    let expected = numbered[tail].number - 1;
    for (let index = tail - 1; index >= 0 && expected >= 1; index -= 1) {
      if (numbered[index].number !== expected) break;
      reverse.push(numbered[index]);
      expected -= 1;
    }
    if (expected !== 0) continue;

    const sequence = reverse.reverse();
    if (provider.length > 0 && sequence.some((claim) => !providerSet.has(claim.number))) {
      continue;
    }
    if (provider.length === 0 && sequence.length < 2) continue;

    const sequenceMax = sequence.at(-1)?.number ?? 0;
    const bestMax = best.at(-1)?.number ?? 0;
    const reachesProviderEnd = providerMax !== null && sequenceMax === providerMax;
    const bestReachesProviderEnd = providerMax !== null && bestMax === providerMax;
    const isBetter =
      (reachesProviderEnd && !bestReachesProviderEnd) ||
      (reachesProviderEnd === bestReachesProviderEnd && sequence.length > best.length) ||
      (reachesProviderEnd === bestReachesProviderEnd &&
        sequence.length === best.length &&
        (sequence.at(-1)?.entryIndex ?? -1) > (best.at(-1)?.entryIndex ?? -1));
    if (isBetter) best = sequence;
  }
  return best;
}

/** The ordinal where the claims section begins, or null if none is detected. */
export function claimsStartOrdinal(entries: EntryDto[]): number | null {
  const explicit = explicitClaimsStartOrdinal(entries);
  if (explicit !== null) return explicit;
  return backwardClaims(entries)[0]?.entry.ordinal ?? null;
}

/** Outline items for each numbered claim, using a heading-first interpretation
 * with a validated end-of-document sequence as the recovery path. */
export function detectClaims(
  entries: EntryDto[],
  providerClaimNumbers: number[] = [],
): OutlineItem[] {
  const explicitStart = explicitClaimsStartOrdinal(entries);
  const forward = explicitStart === null ? [] : forwardClaims(entries, explicitStart);
  const backward = backwardClaims(entries, providerClaimNumbers);

  // Prefer the heading-anchored interpretation unless the independently
  // validated tail scan recovers more of the claims sequence.
  const selected = backward.length > forward.length ? backward : forward;
  return selected.map(claimItem);
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
