import { describe, expect, it } from "vitest";

import type { AnnotationRead, ArtifactEntries, BookmarkRead, DocumentRead } from "../api/types";
import { buildPortable, buildText } from "./export";

const doc: DocumentRead = {
  id: "doc_1",
  title: "US6411897",
  patent_number: "US 6,411,897",
  workspace_id: null,
  state: "ready",
  active_artifact_id: "art_1",
  last_opened_at: null,
  expires_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

const artifact: ArtifactEntries = {
  artifact_id: "art_1",
  doc_type: "grant",
  mode: "native",
  disposition: "complete",
  page_count: 1,
  warnings: [],
  entries: [
    {
      entry_id: "line_0",
      ordinal: 0,
      page_index: 10,
      locator: { kind: "grant", column: 1, printed_line: 1 },
      box: [0.12, 0.1, 0.3, 0.12],
      source_text: "The housing",
      display_text: "The housing",
      text_confidence: "high",
      reference_confidence: "high",
    },
  ],
  figure_mentions: [],
  numeral_mentions: [],
  mention_associations: [],
  callout_occurrences: [],
};

describe("buildPortable", () => {
  const bookmarks: BookmarkRead[] = [
    { id: "b1", document_id: "doc_1", entry_id: "line_0", label: "claim", color: null, created_at: "" },
  ];
  const annotations: AnnotationRead[] = [
    { id: "a1", document_id: "doc_1", target_entry_id: "line_0", note: "a note", created_at: "" },
  ];
  const out = buildPortable(doc, artifact, bookmarks, annotations) as Record<string, unknown>;

  it("emits schema version 2 with the doc type and title", () => {
    expect(out.schema_version).toBe(2);
    expect(out.doc_type).toBe("grant");
    expect(out.title).toBe("US6411897");
  });
  it("carries entries with their typed locator and box", () => {
    const e = (out.entries as Record<string, unknown>[])[0];
    expect(e.locator).toEqual({ kind: "grant", column: 1, printed_line: 1 });
    expect(e.box).toEqual([0.12, 0.1, 0.3, 0.12]);
    expect(e.source_text).toBe("The housing");
  });
  it("references bookmarks and annotations by printed ref", () => {
    expect((out.bookmarks as Record<string, unknown>[])[0]).toMatchObject({ ref: "1:1", label: "claim" });
    expect((out.annotations as Record<string, unknown>[])[0]).toMatchObject({ ref: "1:1", note: "a note" });
  });
});

describe("buildText", () => {
  it("renders one cited line per row", () => {
    expect(buildText(doc, artifact)).toBe("US6411897\n\n1:1\tThe housing\n");
  });
});
