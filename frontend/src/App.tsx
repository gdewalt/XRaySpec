import { useState, useSyncExternalStore } from "react";

import { getToken, setToken, subscribe } from "./auth/session";
import { DocumentsView } from "./components/DocumentsView";
import { Login } from "./components/Login";
import { Viewer } from "./components/Viewer";
import { parseViewHash } from "./spec/navigation";

type View = { mode: "list" } | { mode: "viewer"; documentId: string };

function initialView(): View {
  const { documentId } = parseViewHash(window.location.hash);
  return documentId ? { mode: "viewer", documentId } : { mode: "list" };
}

/**
 * App shell (DESIGN.md §16.1). Gates on a bearer-token session; routes between the
 * Documents list and the reading Viewer. Routing state is mirrored in the URL hash
 * (`#doc=<id>&line=<n>`) so a copied link reopens the document at that line.
 */
export function App() {
  const token = useSyncExternalStore(subscribe, getToken);
  const [view, setView] = useState<View>(initialView);

  const open = (documentId: string) => {
    history.replaceState(null, "", `#doc=${documentId}`);
    setView({ mode: "viewer", documentId });
  };
  const back = () => {
    history.replaceState(null, "", window.location.pathname);
    setView({ mode: "list" });
  };

  let body;
  if (!token) {
    body = <Login />;
  } else if (view.mode === "viewer") {
    body = <Viewer documentId={view.documentId} onBack={back} />;
  } else {
    body = <DocumentsView onOpen={open} />;
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
