import { useEffect, useState, useSyncExternalStore } from "react";

import { getToken, initializeAuth, signOut, subscribe } from "./auth/session";
import { DocumentsView } from "./components/DocumentsView";
import { Icon } from "./components/Icon";
import { Login } from "./components/Login";
import { Viewer } from "./components/Viewer";
import { parseViewHash } from "./spec/navigation";

type View = { mode: "list" } | { mode: "viewer"; documentId: string };

function initialView(): View {
  const { documentId } = parseViewHash(window.location.hash);
  return documentId ? { mode: "viewer", documentId } : { mode: "list" };
}

export function App() {
  const token = useSyncExternalStore(subscribe, getToken);
  const [view, setView] = useState<View>(initialView);

  useEffect(() => {
    void initializeAuth();
  }, []);

  const open = (documentId: string) => {
    history.replaceState(null, "", `#doc=${documentId}`);
    setView({ mode: "viewer", documentId });
  };
  const back = () => {
    history.replaceState(null, "", window.location.pathname);
    setView({ mode: "list" });
  };

  const body = !token ? (
    <Login />
  ) : view.mode === "viewer" ? (
    <Viewer documentId={view.documentId} />
  ) : (
    <DocumentsView onOpen={open} />
  );

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-product">
          <button
            type="button"
            className="brand"
            onClick={token ? back : undefined}
            aria-label={token ? "Open patent library" : "X-Ray Spec"}
          >
            <span className="brand-mark" aria-hidden="true"><Icon name="scan" size={22} /></span>
            <span className="brand-copy">
              <strong>X-Ray Spec</strong>
              <small>Patent evidence workspace</small>
            </span>
          </button>
          {token && (
            <>
              <span className="header-divider" aria-hidden="true" />
              <span className="header-context">
                {view.mode === "viewer" ? "Document viewer" : "Library"}
              </span>
            </>
          )}
        </div>
        <div className="header-actions">
          {token && view.mode === "viewer" && (
            <button type="button" className="secondary header-back" onClick={back}>
              <Icon name="arrow-left" size={17} />
              <span>Library</span>
            </button>
          )}
          {token && (
            <button type="button" className="quiet-button sign-out" onClick={() => void signOut()} aria-label="Sign out" title="Sign out">
              <Icon name="log-out" size={17} />
              <span>Sign out</span>
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
