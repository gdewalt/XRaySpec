// Portable exports (DESIGN.md §16, §11.3): download the document as a
// re-importable portable-save JSON (schema version 2) or as plain cited text.
// Pure builders over the loaded data; the download is a client-side blob.

import type { AnnotationRead, ArtifactEntries, BookmarkRead, DocumentRead } from "../api/types";
import { refShort } from "./navigation";

/** A schema-version-2 portable save the importer round-trips (annotations are an
 * extra, informational field the importer ignores). */
export function buildPortable(
  doc: DocumentRead,
  artifact: ArtifactEntries,
  bookmarks: BookmarkRead[],
  annotations: AnnotationRead[],
): unknown {
  const refOf = new Map(artifact.entries.map((e) => [e.entry_id, refShort(e.locator)]));
  return {
    schema_version: 2,
    doc_type: artifact.doc_type ?? "grant",
    title: doc.title,
    entries: artifact.entries.map((e) => ({
      page_index: e.page_index,
      locator: e.locator,
      box: e.box,
      source_text: e.source_text,
      display_text: e.display_text,
      confidence: e.text_confidence,
      reference_confidence: e.reference_confidence,
    })),
    bookmarks: bookmarks.map((b) => ({ ref: refOf.get(b.entry_id), label: b.label, color: b.color })),
    annotations: annotations.map((a) => ({ ref: refOf.get(a.target_entry_id), note: a.note })),
  };
}

/** The specification as plain text, one cited line per row. */
export function buildText(doc: DocumentRead, artifact: ArtifactEntries): string {
  const body = artifact.entries.map((e) => `${refShort(e.locator)}\t${e.display_text}`).join("\n");
  return `${doc.title}\n\n${body}\n`;
}

function sanitize(name: string): string {
  return name.replace(/[^\w.-]+/g, "_").replace(/^_+|_+$/g, "") || "document";
}

export function download(filename: string, text: string, type: string): void {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function exportPortable(
  doc: DocumentRead,
  artifact: ArtifactEntries,
  bookmarks: BookmarkRead[],
  annotations: AnnotationRead[],
): void {
  const json = JSON.stringify(buildPortable(doc, artifact, bookmarks, annotations), null, 2);
  download(`${sanitize(doc.title)}.xrayspec.json`, json, "application/json");
}

export function exportText(doc: DocumentRead, artifact: ArtifactEntries): void {
  download(`${sanitize(doc.title)}.txt`, buildText(doc, artifact), "text/plain");
}
