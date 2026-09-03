import { useState, useSyncExternalStore } from "react";

import { getToken, setToken, subscribe } from "./auth/session";
import { DocumentsView } from "./components/DocumentsView";
import { Login } from "./components/Login";
import { Viewer } from "./components/Viewer";

type View = { mode: "list" } | { mode: "viewer"; documentId: string };

/**
 * App shell (DESIGN.md §16.1). Gates on a bearer-token session; routes between the
 * Documents list and the reading Viewer (simple state routing, no router lib).
 */
export function App() {
  const token = useSyncExternalStore(subscribe, getToken);
  const [view, setView] = useState<View>({ mode: "list" });

  let body;
  if (!token) {
    body = <Login />;
  } else if (view.mode === "viewer") {
    body = (
      <Viewer documentId={view.documentId} onBack={() => setView({ mode: "list" })} />
    );
  } else {
    body = <DocumentsView onOpen={(id) => setView({ mode: "viewer", documentId: id })} />;
  }

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
      <main className="container">{body}</main>
    </div>
  );
}
