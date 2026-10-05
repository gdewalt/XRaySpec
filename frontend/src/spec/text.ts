import type { EntryDto } from "../api/types";

const BROKEN_WORD = /(\p{L})[-\u2010\u2011]\s*\n\s*(?=\p{L})/gu;

/** Join a word split by a line-ending hyphen without changing real inline hyphens. */
export function dehyphenateLineBreaks(text: string): string {
  return text.replace(BROKEN_WORD, "$1");
}

/** Plain selected prose with paragraph boundaries and wrapped words repaired. */
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
  return dehyphenateLineBreaks(text);
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

