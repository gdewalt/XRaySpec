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
        <button
          type="button"
          className="brand"
          onClick={token ? back : undefined}
          aria-label={token ? "Return to documents" : "X-Ray Spec"}
        >
          <span className="brand-mark" aria-hidden="true">
            <span />
          </span>
          <span className="brand-copy">
            <strong>X-Ray Spec</strong>
            <small>Patent evidence workspace</small>
          </span>
        </button>
        <div className="header-actions">
          {token && view.mode === "viewer" && (
            <button type="button" className="secondary header-back" onClick={back}>
              <span aria-hidden="true">←</span> Documents
            </button>
          )}
          {token && (
            <button type="button" className="quiet-button" onClick={() => setToken(null)}>
              Sign out
            </button>
          )}
        </div>
      </header>
      <main className={`container${view.mode === "viewer" && token ? " viewer-container" : ""}`}>
        {body}
      </main>
    </div>
  );
}
