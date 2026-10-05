import { describe, expect, it } from "vitest";

import type { EntryDto } from "../api/types";
import { CITE_PRESETS, formatCitation, formatRef, shortPatentName } from "./citation";

function grant(ordinal: number, column: number, line: number): EntryDto {
  return {
    entry_id: `l${ordinal}`,
    ordinal,
    page_index: 0,
    locator: { kind: "grant", column, printed_line: line },
    box: null,
    source_text: "x",
    display_text: "x",
    text_confidence: "high",
    reference_confidence: "high",
  };
}

function app(ordinal: number, paragraph: string): EntryDto {
  return {
    entry_id: `l${ordinal}`,
    ordinal,
    page_index: 0,
    locator: { kind: "application", paragraph },
    box: null,
    source_text: "x",
    display_text: "x",
    text_confidence: "high",
    reference_confidence: "high",
  };
}

const columnLine = CITE_PRESETS[0].style;
const standard = CITE_PRESETS[1].style;
const verbose = CITE_PRESETS[2].style;
const compact = CITE_PRESETS[3].style;

describe("formatRef (grant)", () => {
  it("uses column:line by default", () => {
    expect(formatRef([grant(0, 3, 22)], columnLine)).toBe("3:22");
    expect(formatRef([grant(0, 3, 22), grant(1, 3, 25)], columnLine)).toBe("3:22-25");
    expect(formatRef([grant(0, 3, 22), grant(1, 4, 2)], columnLine)).toBe("3:22-4:2");
  });
  it("single line", () => {
    expect(formatRef([grant(0, 3, 22)], standard)).toBe("col. 3, l. 22");
  });
  it("same-column range uses ll.", () => {
    expect(formatRef([grant(0, 3, 22), grant(1, 3, 25)], standard)).toBe("col. 3, ll. 22–25");
  });
  it("cross-column range", () => {
    expect(formatRef([grant(0, 3, 22), grant(1, 4, 2)], standard)).toBe(
      "col. 3, l. 22 – col. 4, l. 2",
    );
  });
  it("verbose words", () => {
    expect(formatRef([grant(0, 3, 22), grant(1, 3, 25)], verbose)).toBe("column 3, lines 22–25");
  });
  it("compact words", () => {
    expect(formatRef([grant(0, 3, 22)], compact)).toBe("3:22");
  });
});

describe("formatRef (application)", () => {
  it("single paragraph", () => {
    expect(formatRef([app(0, "0042")], standard)).toBe("¶ [0042]");
  });
  it("paragraph range", () => {
    expect(formatRef([app(0, "0042"), app(1, "0044")], standard)).toBe("¶¶ [0042]–[0044]");
  });
  it("verbose paragraph word", () => {
    expect(formatRef([app(0, "0042")], verbose)).toBe("paragraph [0042]");
  });
});

describe("formatCitation", () => {
  it("uses the last three patent digits and a column:line range by default", () => {
    expect(formatCitation(
      [grant(0, 1, 15), grant(1, 1, 30)],
      "US 7,840,427 B2",
      columnLine,
    )).toBe(
      "’427 patent 1:15-30",
    );
  });
  it("includes the title when the style asks", () => {
    expect(formatCitation([grant(0, 3, 22)], "US6411897", standard)).toBe(
      "’897 patent col. 3, l. 22",
    );
  });
  it("omits the title under the compact style", () => {
    expect(formatCitation([grant(0, 3, 22)], "US6411897", compact)).toBe("3:22");
  });
  it("is empty for no selection", () => {
    expect(formatCitation([], "US6411897", standard)).toBe("");
  });

  it("finds the patent number without including the kind-code digit", () => {
    expect(shortPatentName("US7840427B2")).toBe("’427 patent");
    expect(shortPatentName("US 7,840,427 B2")).toBe("’427 patent");
  });
});
