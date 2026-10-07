import { describe, expect, it } from "vitest";

import type { EntryDto } from "../api/types";
import {
  dehyphenateLineBreaks,
  deriveIndentLevels,
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
});

