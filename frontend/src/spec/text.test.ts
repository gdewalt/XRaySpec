import { describe, expect, it } from "vitest";

import type { EntryDto } from "../api/types";
import {
  dehyphenateLineBreaks,
  deriveIndentLevels,
  deriveParagraphStarts,
  formatSelectionWithCitation,
  joinEntryText,
  normalizeCopiedText,
} from "./text";

function entry(
  id: string,
  text: string,
  box: number[],
  options: Partial<EntryDto> = {},
): EntryDto {
  return {
    entry_id: id,
    ordinal: Number(id.slice(1)),
    page_index: 0,
    locator: { kind: "grant", column: 1, printed_line: 1 },
    box,
    source_text: text,
    display_text: text,
    text_confidence: "medium",
    reference_confidence: "medium",
    ...options,
  };
}

describe("text reconstruction", () => {
  it("joins words broken by a line-ending hyphen", () => {
    expect(dehyphenateLineBreaks("The trans-\nmitter operates")).toBe(
      "The transmitter operates",
    );
    expect(dehyphenateLineBreaks("a well-known design")).toBe("a well-known design");
  });

  it("copies selected prose on one line while repairing wrapped words", () => {
    const entries = [
      entry("e0", "A trans-", [0.1, 0.1, 0.4, 0.12]),
      entry("e1", "mitter operates.", [0.1, 0.13, 0.4, 0.15]),
      entry("e2", "A new paragraph.", [0.12, 0.18, 0.4, 0.2], {
        paragraph_start: true,
      }),
    ];
    expect(joinEntryText(entries)).toBe("A transmitter operates. A new paragraph.");
  });

  it("copies provider-corrected text instead of noisy OCR source text", () => {
    const entries = [
      entry("e0", "nables regular traffic", [0.1, 0.1, 0.4, 0.12], {
        display_text: "regular traffic",
      }),
    ];
    expect(joinEntryText(entries)).toBe("regular traffic");
  });

  it("flattens line, paragraph, and tab whitespace only for copied text", () => {
    expect(normalizeCopiedText("\tFirst line\n\nSecond\tline  here ")).toBe(
      "First line Second line here",
    );
  });

  it("quotes selected text before its citation", () => {
    expect(formatSelectionWithCitation("A trans-\nmitter operates.", "’427 patent 1:15-16")).toBe(
      "“A transmitter operates.” ’427 patent 1:15-16",
    );
  });

  it("derives a leading tab from OCR line geometry", () => {
    const entries = [
      entry("e0", "baseline", [0.1, 0.1, 0.4, 0.12]),
      entry("e1", "baseline", [0.1, 0.13, 0.4, 0.15]),
      entry("e2", "indented", [0.114, 0.16, 0.4, 0.18]),
    ];
    expect(deriveIndentLevels(entries).get("e2")).toBe(1);
  });

  it("keeps the real 1:31 indent and suppresses legacy false breaks in US 7,840,427", () => {
    const lines = [30, 31, 38, 39, 40, 43, 44].map((line, index) =>
      entry(`e${line}`, `line ${line}`, [line === 31 ? 0.143 : 0.128, 0.46 + index * 0.013, 0.44, 0.48 + index * 0.013], {
        locator: { kind: "grant", column: 1, printed_line: line },
        paragraph_start: [31, 39, 40, 44].includes(line),
        indent_level: line === 31 ? 2 : 0,
      }),
    );

    expect([...deriveParagraphStarts(lines)]).toEqual(["e31"]);
  });

  it("trusts an explicitly numbered USPTO paragraph even when it is flush left", () => {
    const entries = [
      entry("e0", "prior", [0.1, 0.1, 0.4, 0.12]),
      entry("e1", "numbered paragraph", [0.1, 0.12, 0.4, 0.14], {
        paragraph_start: true,
        paragraph_source: "uspto_numbered",
      }),
    ];
    expect(deriveParagraphStarts(entries).has("e1")).toBe(true);
  });
});

