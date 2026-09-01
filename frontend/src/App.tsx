import { useEffect, useState } from "react";

/**
 * Scaffold shell. The real app is a Documents view + a viewer (resizable panes,
 * PDF.js overlay, SSE progress) built against the FastAPI JSON API. See
 * DESIGN.md §16. API types are generated from the backend OpenAPI schema
 * (`npm run gen:api`, DESIGN.md §25.2).
 */
export function App() {
  const [status, setStatus] = useState<string>("checking…");

  useEffect(() => {
    fetch("/ready")
      .then((r) => r.json())
      .then((d) => setStatus(String(d.status)))
      .catch(() => setStatus("unreachable"));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 640 }}>
      <h1>X-Ray Spec</h1>
      <p>Hosted patent specification viewer — scaffold.</p>
      <p>
        Backend status: <strong>{status}</strong>
      </p>
      <p style={{ color: "#666" }}>See DESIGN.md for the build plan.</p>
    </main>
  );
}
