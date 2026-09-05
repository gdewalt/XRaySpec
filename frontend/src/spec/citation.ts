// Citation profiles (DESIGN.md §16, Phase 4): configurable formatting of a
// col:line (grant) or paragraph (application) reference, with a few options and
// presets, persisted per browser. Pure over the selected entries.

import type { EntryDto } from "../api/types";

export type CiteStyle = {
  includeTitle: boolean;
  columnWord: "col." | "column" | "c.";
  lineWord: "l." | "line";
  paragraphWord: "¶" | "para." | "paragraph";
};

export type CitePreset = { id: string; name: string; style: CiteStyle };

export const CITE_PRESETS: CitePreset[] = [
  {
    id: "standard",
    name: "Standard (col. 3, l. 22)",
    style: { includeTitle: true, columnWord: "col.", lineWord: "l.", paragraphWord: "¶" },
  },
  {
    id: "verbose",
    name: "Verbose (column 3, line 22)",
    style: { includeTitle: true, columnWord: "column", lineWord: "line", paragraphWord: "paragraph" },
  },
  {
    id: "compact",
    name: "Compact (c. 3, l. 22 — no title)",
    style: { includeTitle: false, columnWord: "c.", lineWord: "l.", paragraphWord: "para." },
  },
];

export const DEFAULT_STYLE: CiteStyle = CITE_PRESETS[0].style;

const KEY = "xray_cite_style";

export function loadStyle(): CiteStyle {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULT_STYLE, ...(JSON.parse(raw) as Partial<CiteStyle>) };
  } catch {
    /* ignore */
  }
  return DEFAULT_STYLE;
}

export function saveStyle(style: CiteStyle): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(style));
  } catch {
    /* private mode: keep the in-memory value only */
  }
}

/** The reference part (no title) for a selection under a style. */
export function formatRef(entries: EntryDto[], style: CiteStyle): string {
  if (entries.length === 0) return "";
  const a = entries[0].locator;
  const b = entries[entries.length - 1].locator;
  const lineWord = (plural: boolean) =>
    style.lineWord === "l." ? (plural ? "ll." : "l.") : plural ? "lines" : "line";

  if (a.kind === "grant" && b.kind === "grant") {
    if (a.column === b.column) {
      return a.printed_line === b.printed_line
        ? `${style.columnWord} ${a.column}, ${lineWord(false)} ${a.printed_line}`
        : `${style.columnWord} ${a.column}, ${lineWord(true)} ${a.printed_line}–${b.printed_line}`;
    }
    return (
      `${style.columnWord} ${a.column}, ${lineWord(false)} ${a.printed_line} – ` +
      `${style.columnWord} ${b.column}, ${lineWord(false)} ${b.printed_line}`
    );
  }
  if (a.paragraph && b.paragraph) {
    const p = style.paragraphWord;
    const one = (v: string) => (p === "¶" ? `¶ [${v}]` : `${p} [${v}]`);
    if (a.paragraph === b.paragraph) return one(a.paragraph);
    const many = p === "¶" ? "¶¶" : `${p}s`;
    return `${many} [${a.paragraph}]–[${b.paragraph}]`;
  }
  return "";
}

/** Full citation: optional title + reference. */
export function formatCitation(entries: EntryDto[], title: string, style: CiteStyle): string {
  const ref = formatRef(entries, style);
  if (!ref) return "";
  return style.includeTitle && title ? `${title}, ${ref}` : ref;
}
