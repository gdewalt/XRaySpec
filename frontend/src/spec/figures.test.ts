import { describe, expect, it } from "vitest";

import type { EntryDto } from "../api/types";
import { detectDisplayFigureMentions } from "./figures";

function entry(sourceText: string, displayText: string): EntryDto {
  return {
    entry_id: "e1",
    ordinal: 1,
    page_index: 0,
    locator: { kind: "grant", column: 1, printed_line: 1 },
    box: [0.1, 0.1, 0.4, 0.12],
    source_text: sourceText,
    display_text: displayText,
    text_confidence: "medium",
    reference_confidence: "medium",
  };
}

describe("display-text figure references", () => {
  it("uses corrected display text rather than the OCR source", () => {
    const text = "As shown in FIG. 14A, the controller responds.";
    const mentions = detectDisplayFigureMentions([
      entry("As shown in FlG. l4A, the controller responds.", text),
    ]);

    expect(mentions).toEqual([
      {
        entry_id: "e1",
        raw_text: "FIG. 14A",
        span: [12, 20],
        figure_ids: ["14A"],
      },
    ]);
  });

  it("expands numeric and sub-figure ranges", () => {
    const [numeric, subfigures] = detectDisplayFigureMentions([
      entry("", "FIGS. 4–6 correspond to FIGS. 14A-14C."),
    ]);

    expect(numeric.figure_ids).toEqual(["4", "5", "6"]);
    expect(subfigures.figure_ids).toEqual(["14A", "14B", "14C"]);
  });
});
