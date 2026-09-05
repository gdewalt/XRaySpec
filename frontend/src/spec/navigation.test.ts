import { describe, expect, it } from "vitest";

import type { EntryDto } from "../api/types";
import {
  buildViewHash,
  claimsStartOrdinal,
  detectOutline,
  highlightSegments,
  parseViewHash,
  refShort,
  scopedEntries,
  searchEntries,
} from "./navigation";

function grant(ordinal: number, line: number, text: string, column = 1): EntryDto {
  return {
    entry_id: `line_${ordinal}`,
    ordinal,
    page_index: 0,
    locator: { kind: "grant", column, printed_line: line },
    box: [0.1, 0.1, 0.9, 0.12],
    source_text: text,
    display_text: text,
    text_confidence: "high",
    reference_confidence: "high",
  };
}

describe("refShort", () => {
  it("renders grant and application locators", () => {
    expect(refShort({ kind: "grant", column: 3, printed_line: 22 })).toBe("3:22");
    expect(refShort({ kind: "application", paragraph: "0042" })).toBe("[0042]");
  });
});

describe("deep-link hash", () => {
  it("round-trips a document + single line", () => {
    const h = buildViewHash("doc_1", 5);
    expect(parseViewHash(h)).toEqual({ documentId: "doc_1", start: 5, end: 5 });
  });
  it("round-trips a range and orders it", () => {
    expect(parseViewHash(buildViewHash("d", 8, 3))).toMatchObject({ start: 3, end: 8 });
  });
  it("parses a doc-only hash", () => {
    expect(parseViewHash("#doc=abc")).toEqual({ documentId: "abc" });
  });
  it("returns nothing useful for an empty hash", () => {
    expect(parseViewHash("")).toEqual({ documentId: undefined });
  });
});

describe("search", () => {
  const entries = [grant(0, 1, "The housing 104"), grant(1, 2, "receives the shaft"), grant(2, 3, "HOUSING again")];
  it("matches case-insensitively and returns ordinals", () => {
    expect(searchEntries(entries, "housing")).toEqual([0, 2]);
  });
  it("returns nothing for a blank query", () => {
    expect(searchEntries(entries, "  ")).toEqual([]);
  });
  it("segments a line around matches", () => {
    expect(highlightSegments("aXbXc", "x")).toEqual([
      { text: "a", hit: false },
      { text: "X", hit: true },
      { text: "b", hit: false },
      { text: "X", hit: true },
      { text: "c", hit: false },
    ]);
  });
});

describe("outline + scopes", () => {
  const entries = [
    grant(0, 1, "APPARATUS FOR SIGNAL RECEPTION"),
    grant(1, 3, "Some prose here."),
    grant(2, 5, "BACKGROUND OF THE INVENTION"),
    grant(3, 7, "What is claimed is:"),
    grant(4, 8, "1. A method comprising steps."),
  ];
  it("detects all-caps headings and figures", () => {
    const figFirst = new Map([["3", 1]]);
    const items = detectOutline(entries, figFirst);
    const labels = items.map((i) => i.label);
    expect(labels).toContain("APPARATUS FOR SIGNAL RECEPTION");
    expect(labels).toContain("BACKGROUND OF THE INVENTION");
    expect(items.some((i) => i.kind === "figure" && i.label === "FIG. 3")).toBe(true);
    expect(labels).not.toContain("Some prose here.");
  });
  it("finds where claims begin", () => {
    expect(claimsStartOrdinal(entries)).toBe(3);
  });
  it("scopes to claims from that ordinal", () => {
    const sub = scopedEntries(entries, "claims", new Set());
    expect(sub.map((e) => e.ordinal)).toEqual([3, 4]);
  });
  it("scopes to figure-bearing lines, falling back to all when none", () => {
    expect(scopedEntries(entries, "figures", new Set(["line_1"])).map((e) => e.ordinal)).toEqual([1]);
    expect(scopedEntries(entries, "figures", new Set()).length).toBe(entries.length);
  });
});
