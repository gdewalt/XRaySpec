// Typed client over the FastAPI JSON API. Adds the bearer token, normalizes
// errors, and models the multi-step ingestion flows (DESIGN.md §11, §14).

import { getToken } from "../auth/session";
import type {
  ArtifactEntries,
  DocumentList,
  DocumentRead,
  ImportAnalysis,
  JobRead,
  UploadCompleteResponse,
  UploadGrant,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(path, { ...init, headers });
  if (res.status === 204) return undefined as T;

  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail ?? data?.error?.message ?? res.statusText;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  listDocuments: () => request<DocumentList>("/api/v1/documents"),

  getDocument: (id: string) => request<DocumentRead>(`/api/v1/documents/${id}`),

  getDocumentJob: (id: string) => request<JobRead>(`/api/v1/documents/${id}/job`),

  getArtifactEntries: (artifactId: string) =>
    request<ArtifactEntries>(`/api/v1/artifacts/${artifactId}/entries`),

  createFetch: (patentIdentifier: string, title?: string) =>
    request<DocumentRead>(
      "/api/v1/documents",
      jsonInit("POST", {
        source_type: "fetch",
        patent_identifier: patentIdentifier,
        title: title || null,
      }),
    ),

  deleteDocument: (id: string) => request<void>(`/api/v1/documents/${id}`, { method: "DELETE" }),

  createUploadGrant: (filename: string, contentType: string) =>
    request<UploadGrant>(
      "/api/v1/uploads",
      jsonInit("POST", { filename, content_type: contentType }),
    ),

  completeUpload: (uploadId: string, title?: string) =>
    request<UploadCompleteResponse>(
      `/api/v1/uploads/${uploadId}/complete`,
      jsonInit("POST", { title: title || null }),
    ),

  analyzeImport: (bytes: ArrayBuffer) =>
    request<ImportAnalysis>("/api/v1/imports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: bytes,
    }),

  commitImport: (id: string, opts: { title?: string; confirm_migration?: boolean }) =>
    request<DocumentRead>(`/api/v1/imports/${id}/commit`, jsonInit("POST", opts)),
};

/** Full direct-upload flow: grant -> PUT to private storage -> finalize. */
export async function uploadPdf(file: File, title?: string): Promise<UploadCompleteResponse> {
  const grant = await api.createUploadGrant(file.name, file.type || "application/pdf");
  const put = await fetch(grant.url, { method: grant.method, headers: grant.headers, body: file });
  if (!put.ok) {
    throw new ApiError(put.status, `Upload to storage failed (${put.status}).`);
  }
  return api.completeUpload(grant.upload_id, title);
}
