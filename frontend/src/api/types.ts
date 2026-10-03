// Wire types mirroring the FastAPI schemas (DESIGN.md §14).
// When the backend is running, `npm run gen:api` regenerates these from its
// OpenAPI schema; kept hand-written here so the app builds without a live server.

export interface DocumentRead {
  id: string;
  title: string;
  workspace_id: string | null;
  state: string;
  active_artifact_id: string | null;
  last_opened_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface DocumentList {
  items: DocumentRead[];
}

export interface WorkspaceRead {
  id: string;
  name: string;
  created_at: string;
}

export interface WorkspaceList {
  items: WorkspaceRead[];
}

export interface BookmarkRead {
  id: string;
  document_id: string;
  entry_id: string;
  label: string | null;
  color: string | null;
  created_at: string;
}

export interface AnnotationRead {
  id: string;
  document_id: string;
  target_entry_id: string;
  note: string;
  created_at: string;
}

export interface OverrideRead {
  id: string;
  document_id: string;
  entry_id: string;
  span_start: number;
  span_end: number;
  callout_id: string | null;
  created_at: string;
}

export interface JobRead {
  id: string;
  document_id: string | null;
  status: string; // queued | running | succeeded | failed | cancelled
  stage: string | null;
  stage_label: string | null;
  attempt: number;
  completed_units: number;
  total_units: number | null;
  unit: string | null;
  overall_fraction: number | null;
  indeterminate: boolean;
  cancel_requested: boolean;
  failure_code: string | null;
  progress_sequence: number;
  created_at: string;
  finished_at: string | null;
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

export interface CalloutDto {
  callout_id: string;
  value: string;
  page_index: number;
  box: number[];
  figure_id: string | null;
  confidence: number | null;
}

export interface ArtifactEntries {
  artifact_id: string;
  doc_type: string | null;
  mode: string | null;
  disposition: string | null;
  page_count: number | null;
  warnings: string[];
  entries: EntryDto[];
  figure_mentions: FigureMentionDto[];
  numeral_mentions: NumeralMentionDto[];
  mention_associations: AssociationDto[];
  callout_occurrences: CalloutDto[];
}
