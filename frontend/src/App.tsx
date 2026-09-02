import { useSyncExternalStore } from "react";

import { getToken, setToken, subscribe } from "./auth/session";
import { DocumentsView } from "./components/DocumentsView";
import { Login } from "./components/Login";

/**
 * App shell (DESIGN.md §16.1). Gates on a bearer-token session; renders the
 * Documents view (list + ingestion) when signed in, otherwise the sign-in panel.
 */
export function App() {
  const token = useSyncExternalStore(subscribe, getToken);

  return (
    <div className="app">
      <header className="app-header">
        <h1>X-Ray Spec</h1>
        {token && (
          <button type="button" className="secondary" onClick={() => setToken(null)}>
            Sign out
          </button>
        )}
      </header>
      <main className="container">{token ? <DocumentsView /> : <Login />}</main>
    </div>
  );
}
