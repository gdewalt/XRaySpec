// Minimal bearer-token session (DESIGN.md §17.1).
//
// The production path is Supabase Auth (magic-link / Google), whose SDK yields a
// JWT the backend verifies. Until that is wired, this stores a pasted token so
// the UI is usable in development. The token lives only in this browser.

const KEY = "xray_token";

function read(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

let token: string | null = read();
const listeners = new Set<() => void>();

export function getToken(): string | null {
  return token;
}

export function setToken(next: string | null): void {
  token = next;
  try {
    if (next) localStorage.setItem(KEY, next);
    else localStorage.removeItem(KEY);
  } catch {
    /* private mode: keep the in-memory token only */
  }
  listeners.forEach((l) => l());
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
