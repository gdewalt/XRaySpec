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
