/**
 * Thin API client over the FastAPI JSON API.
 *
 * Generate typed definitions from the backend OpenAPI schema with
 * `npm run gen:api` (writes src/api/schema.ts), then type requests/responses
 * against it so the client cannot drift off the server contract (DESIGN.md §25.2).
 */

const BASE = "/api/v1";

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return (await res.json()) as T;
}
