import { describe, expect, it } from "vitest";

import type { EntryDto, PdfAnnotationRead } from "../api/types";
import {
  adjustedPdfScale,
  lineHighlightGeometry,
  removeAnnotationById,
  simplifyDrawingPoints,
} from "./PdfPane";

function entry(ordinal: number, text: string, box: number[]): EntryDto {
  return {
    entry_id: `entry_${ordinal}`,
    ordinal,
    page_index: 0,
    locator: { kind: "grant", column: 1, printed_line: ordinal + 1 },
    box,
    source_text: text,
    display_text: text,
    text_confidence: "high",
    reference_confidence: "high",
  };
}

describe("PDF magnification", () => {
  it("moves one step in either direction", () => {
    expect(adjustedPdfScale(1.15, 1)).toBe(1.3);
    expect(adjustedPdfScale(1.15, -1)).toBe(1);
  });

  it("stays inside the supported scale range", () => {
    expect(adjustedPdfScale(0.5, -1)).toBe(0.5);
    expect(adjustedPdfScale(3, 1)).toBe(3);
  });
});

describe("PDF annotation geometry", () => {
  it("anchors a partial highlight to text offsets and snapped line geometry", () => {
    const result = lineHighlightGeometry(
      [0.26, 0.15],
      [0.74, 0.15],
      [entry(0, "abcdefghij", [0.1, 0.1, 0.9, 0.2])],
    );

    expect(result.textRanges).toEqual([
      { entry_id: "entry_0", start: 2, end: 8, quote: "cdefgh" },
    ]);
    expect(result.rects[0][0]).toBeCloseTo(0.26);
    expect(result.rects[0][2]).toBeCloseTo(0.74);
  });

  it("keeps endpoints while reducing dense freehand paths", () => {
    const points: [number, number][] = [
      [0.1, 0.1],
      [0.1001, 0.1001],
      [0.2, 0.2],
      [0.2001, 0.2001],
      [0.3, 0.3],
    ];
    expect(simplifyDrawingPoints(points, 0.01)).toEqual([
      [0.1, 0.1],
      [0.2, 0.2],
      [0.3, 0.3],
    ]);
  });

  it("removes the captured annotation id from local history state", () => {
    const annotation = (id: string): PdfAnnotationRead => ({
      id,
      document_id: "doc_1",
      kind: "highlight",
      page_index: 0,
      geometry: { x0: 0.1, y0: 0.1, x1: 0.2, y1: 0.2 },
      color: "#ffe066",
      note: null,
      created_at: "2026-10-25T00:00:00Z",
    });

    expect(removeAnnotationById([annotation("keep"), annotation("remove")], "remove"))
      .toEqual([annotation("keep")]);
  });
});
