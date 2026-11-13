import { describe, expect, it } from "vitest";

import type { EntryDto } from "../api/types";
import { textHighlightPages } from "./Viewer";

function entry(id: string, pageIndex: number, text: string, box: number[]): EntryDto {
  return {
    entry_id: id,
    ordinal: Number(id.replace(/\D/g, "")) || 0,
    page_index: pageIndex,
    locator: { kind: "grant", column: 1, printed_line: 1 },
    box,
    source_text: text,
    display_text: text,
    text_confidence: "high",
    reference_confidence: "high",
  };
}

describe("text highlight geometry", () => {
  it("maps a partial text selection onto its PDF line", () => {
    const result = textHighlightPages([
      { entry: entry("entry_1", 2, "abcdefghij", [0.1, 0.2, 0.9, 0.3]), start: 2, end: 7 },
    ]);

    expect(result).toHaveLength(1);
    expect(result[0].pageIndex).toBe(2);
    expect(result[0].ranges).toEqual([
      { entry_id: "entry_1", start: 2, end: 7, quote: "cdefg" },
    ]);
    expect(result[0].rects[0][0]).toBeCloseTo(0.26);
    expect(result[0].rects[0][2]).toBeCloseTo(0.66);
  });

  it("creates separate persisted annotations for selections spanning PDF pages", () => {
    const result = textHighlightPages([
      { entry: entry("entry_1", 0, "first", [0.1, 0.2, 0.9, 0.3]), start: 0, end: 5 },
      { entry: entry("entry_2", 1, "second", [0.1, 0.2, 0.9, 0.3]), start: 0, end: 6 },
    ]);

    expect(result.map((page) => page.pageIndex)).toEqual([0, 1]);
    expect(result.every((page) => page.ranges.length === 1)).toBe(true);
  });
});
