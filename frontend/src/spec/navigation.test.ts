import { describe, expect, it } from "vitest";

import type { EntryDto } from "../api/types";
import {
  buildViewHash,
  buildFigureOutline,
  claimsStartOrdinal,
  detectClaims,
  detectOutline,
  resolveFigurePage,
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
  it("detects all-caps specification headings without mixing in figures", () => {
    const items = detectOutline(entries);
    const labels = items.map((i) => i.label);
    expect(labels).not.toContain("APPARATUS FOR SIGNAL RECEPTION");
    expect(labels).toContain("BACKGROUND OF THE INVENTION");
    expect(items.some((i) => i.kind !== "heading")).toBe(false);
    expect(labels).not.toContain("Some prose here.");
  });
  it("starts at specification headings and joins wrapped headings from US 7,840,427", () => {
    const patent = [
      grant(0, 1, "SHARED TRANSPORT SYSTEMAND"),
      grant(1, 2, "SERVICENETWORK"),
      grant(2, 6, "FIELD OF THE INVENTION"),
      grant(3, 13, "BACKGROUND OF THE INVENTION"),
      grant(4, 5, "DETAILED DESCRIPTION OF THE PREFERRED", 7),
      grant(5, 6, "EMBODIMENTS", 7),
      grant(6, 10, "What is claimed is:", 16),
      grant(7, 11, "1. A shared transportation system.", 16),
    ];

    expect(detectOutline(patent).map((item) => item.label)).toEqual([
      "FIELD OF THE INVENTION",
      "BACKGROUND OF THE INVENTION",
      "DETAILED DESCRIPTION OF THE PREFERRED EMBODIMENTS",
    ]);
  });
  it("keeps figures and claims out of the specification outline", () => {
    const withClaimsHeading = [
      grant(0, 1, "BACKGROUND OF THE INVENTION"),
      grant(1, 2, "FIG. 2 illustrates an embodiment."),
      grant(2, 3, "DETAILED DESCRIPTION"),
      grant(3, 4, "CLAIMS"),
      grant(4, 5, "1. A method comprising steps."),
      grant(5, 6, "APPARATUS", 2),
    ];

    const items = detectOutline(withClaimsHeading);

    expect(items.map((item) => item.label)).toEqual([
      "BACKGROUND OF THE INVENTION",
      "DETAILED DESCRIPTION",
    ]);
  });
  it("finds where claims begin", () => {
    expect(claimsStartOrdinal(entries)).toBe(3);
  });
  it("lists sequentially numbered claims and ignores stray numbers", () => {
    const withClaims = [
      grant(0, 1, "The invention relates to 1. widgets in prose."),
      grant(1, 5, "What is claimed is:"),
      grant(2, 6, "1. A method comprising steps."),
      grant(3, 9, "2. The method of claim 1 wherein."),
      grant(4, 12, "5. A non-sequential number here."),
      grant(5, 14, "3. The method of claim 2."),
    ];
    const items = detectClaims(withClaims);
    expect(items.map((i) => i.label)).toEqual(["Claim 1", "Claim 2", "Claim 3"]);
    expect(items[0].ref).toBe("1:6");
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

describe("figure page resolution", () => {
  const figure = (figureId: string, pageIndex: number) => ({
    figure_id: figureId,
    page_index: pageIndex,
    box: [0.1, 0.2, 0.3, 0.4] as [number, number, number, number],
    confidence: null,
  });

  it("prefers an exact figure occurrence", () => {
    expect(resolveFigurePage(["1"], [figure("1", 4), figure("2", 5)])).toBe(4);
  });

  it("recovers a legacy missing FIG. 1 from the following FIG. 2 sheet", () => {
    expect(resolveFigurePage(["1"], [figure("2", 2), figure("3", 3)])).toBe(1);
  });

  it("does not guess for other missing figures", () => {
    expect(resolveFigurePage(["7"], [figure("8", 9)])).toBeNull();
  });

  it("builds a naturally ordered figure outline from mentions and drawing occurrences", () => {
    const mentions = [
      { entry_id: "line_1", raw_text: "FIGS. 2 and 10", span: [0, 14] as [number, number], figure_ids: ["2", "10"] },
      { entry_id: "line_2", raw_text: "FIG. 1", span: [0, 6] as [number, number], figure_ids: ["1"] },
    ];
    expect(buildFigureOutline(mentions, [figure("FIG. 10", 5), figure("2", 3), figure("1", 2)])).toEqual([
      { figureId: "1", label: "FIG. 1", page: 3 },
      { figureId: "2", label: "FIG. 2", page: 4 },
      { figureId: "10", label: "FIG. 10", page: 6 },
    ]);
  });
});
