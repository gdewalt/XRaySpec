// Wire types mirroring the FastAPI schemas (DESIGN.md §14).
// When the backend is running, `npm run gen:api` regenerates these from its
// OpenAPI schema; kept hand-written here so the app builds without a live server.

export interface DocumentRead {
  id: string;
  title: string;
  state: string;
  active_artifact_id: string | null;
  last_opened_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface DocumentList {
  items: DocumentRead[];
}

export interface UploadGrant {
  upload_id: string;
  object_key: string;
  url: string;
  method: string;
  headers: Record<string, string>;
  max_bytes: number;
  expires_at: string;
}

export interface UploadCompleteResponse {
  document: DocumentRead;
  job_id: string;
  job_status: string;
}

export interface ImportAnalysis {
  import_id: string;
  schema_version: number;
  needs_migration: boolean;
  doc_type: string | null;
  title: string | null;
  entry_count: number;
  bookmark_count: number;
  imported_unverified: boolean;
  warnings: string[];
}

export interface Locator {
  kind: "grant" | "application";
  column?: number;
  printed_line?: number;
  paragraph?: string;
}

export interface EntryDto {
  entry_id: string;
  ordinal: number;
  page_index: number;
  locator: Locator;
  box: number[] | null;
  source_text: string;
  display_text: string;
  text_confidence: string;
  reference_confidence: string;
}

export interface FigureMentionDto {
  entry_id: string;
  raw_text: string;
  span: [number, number];
  figure_ids: string[];
}

export interface NumeralMentionDto {
  entry_id: string;
  value: string;
  component_label: string | null;
  span: [number, number];
}

export interface AssociationDto {
  entry_id: string;
  value: string;
  span: [number, number];
  status: string;
  selected_callout_ids: string[];
  candidate_callout_ids: string[];
}

export interface ArtifactEntries {
  artifact_id: string;
  doc_type: string | null;
  mode: string | null;
  disposition: string | null;
  page_count: number | null;
  entries: EntryDto[];
  figure_mentions: FigureMentionDto[];
  numeral_mentions: NumeralMentionDto[];
  mention_associations: AssociationDto[];
  callout_occurrences: Record<string, unknown>[];
}
