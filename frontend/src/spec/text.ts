import type { EntryDto } from "../api/types";

const BROKEN_WORD = /(\p{L})[-\u2010\u2011]\s*\n\s*(?=\p{L})/gu;

/** Join a word split by a line-ending hyphen without changing real inline hyphens. */
export function dehyphenateLineBreaks(text: string): string {
  return text.replace(BROKEN_WORD, "$1");
}

/** Clipboard prose: repair wrapped words and flatten visual lines to one line. */
export function normalizeCopiedText(text: string): string {
  return dehyphenateLineBreaks(text).replace(/\s+/gu, " ").trim();
}

/** Citation-ready clipboard text with the selected prose quoted first. */
export function formatSelectionWithCitation(text: string, citation: string): string {
  const selection = normalizeCopiedText(text);
  const reference = citation.trim();
  if (!selection) return reference;
  return `“${selection}”${reference ? ` ${reference}` : ""}`;
}

/** Plain selected prose for the clipboard. Source/display text is never changed. */
export function joinEntryText(entries: EntryDto[]): string {
  let text = "";
  entries.forEach((entry, index) => {
    if (index > 0) {
      const previous = entries[index - 1];
      const newParagraph =
        entry.paragraph_start === true ||
        (!!entry.locator.paragraph && entry.locator.paragraph !== previous.locator.paragraph);
      text += newParagraph ? "\n\n" : "\n";
    }
    text += entry.source_text;
  });
  return normalizeCopiedText(text);
}

/**
 * Derive leading tabs from line geometry as a fallback for older artifacts, then
 * combine them with any indentation explicitly detected by the extractor.
 */
export function deriveIndentLevels(entries: EntryDto[]): Map<string, number> {
  const groups = new Map<string, EntryDto[]>();
  for (const entry of entries) {
    if (!entry.box || entry.box.length !== 4) continue;
    const column = entry.locator.kind === "grant" ? entry.locator.column ?? 0 : 0;
    const key = `${entry.page_index}:${entry.locator.kind}:${column}`;
    const group = groups.get(key);
    if (group) group.push(entry);
    else groups.set(key, [entry]);
  }

  const levels = new Map<string, number>();
  for (const group of groups.values()) {
    const leftEdges = group
      .map((entry) => entry.box?.[0])
      .filter((value): value is number => typeof value === "number")
      .sort((a, b) => a - b);
    if (leftEdges.length === 0) continue;
    const baseline = leftEdges[Math.floor((leftEdges.length - 1) * 0.2)];
    for (const entry of group) {
      const offset = Math.max(0, (entry.box?.[0] ?? baseline) - baseline);
      const geometric = offset >= 0.006 ? Math.max(1, Math.min(6, Math.round(offset / 0.012))) : 0;
      levels.set(entry.entry_id, Math.max(entry.indent_level ?? 0, geometric));
    }
  }
  return levels;
}

/**
 * Resolve paragraph spacing while remaining compatible with older artifacts.
 * New artifacts identify the evidence that created a break. Older ones are
 * accepted only when their geometry corroborates the flag, which suppresses
 * noisy OCR/provider boundaries without losing a real first-line indent.
 */
export function deriveParagraphStarts(entries: EntryDto[]): Set<string> {
  const starts = new Set<string>();
  const groups = new Map<string, EntryDto[]>();

  for (const entry of entries) {
    if (entry.locator.kind === "application") {
      const previous = entries[entry.ordinal - 1];
      if (
        entry.paragraph_start === true ||
        (!!entry.locator.paragraph && entry.locator.paragraph !== previous?.locator.paragraph)
      ) {
        starts.add(entry.entry_id);
      }
      continue;
    }
    const key = `${entry.page_index}:${entry.locator.column ?? 0}`;
    const group = groups.get(key);
    if (group) group.push(entry);
    else groups.set(key, [entry]);
  }

  for (const unsorted of groups.values()) {
    const group = [...unsorted].sort((a, b) => a.ordinal - b.ordinal);
    const leftEdges = group
      .map((entry) => entry.box?.[0])
      .filter((value): value is number => typeof value === "number")
      .sort((a, b) => a - b);
    const baseline = leftEdges[Math.floor(Math.max(0, leftEdges.length - 1) * 0.2)];
    const gaps = group
      .slice(1)
      .map((entry, index) => {
        const previous = group[index];
        return entry.box && previous.box ? entry.box[1] - previous.box[1] : 0;
      })
      .filter((gap) => gap > 0)
      .sort((a, b) => a - b);
    const typicalGap = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0;

    group.forEach((entry, index) => {
      if (entry.paragraph_start !== true) return;
      // New artifacts carry explicit, already-vetted evidence.
      if (entry.paragraph_source) {
        starts.add(entry.entry_id);
        return;
      }

      const previous = group[index - 1];
      const indented =
        (entry.indent_level ?? 0) > 0 ||
        (typeof baseline === "number" && !!entry.box && entry.box[0] - baseline >= 0.006);
      const verticalGap = entry.box && previous?.box ? entry.box[1] - previous.box[1] : 0;
      const spaced = typicalGap > 0 && verticalGap >= Math.max(0.018, typicalGap * 1.6);
      if (indented || spaced) starts.add(entry.entry_id);
    });
  }

  return starts;
}

