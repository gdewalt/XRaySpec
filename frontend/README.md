# X-Ray Spec — frontend

React + TypeScript SPA (Vite) over the FastAPI JSON API. See root
[DESIGN.md](../DESIGN.md) §16 (UX) and §25.2 (stack).

## Quickstart

```bash
npm install
npm run dev        # http://localhost:5173 (proxies /api to :8000)
npm run typecheck
npm run build
```

## API types

Types are generated from the backend's OpenAPI schema so the client stays in
lockstep with the server:

```bash
# with the backend running on :8000
npm run gen:api    # -> src/api/schema.ts
```

## What lands here (DESIGN.md §16)

- Documents view (list, ingestion, states)
- Job progress via SSE with polling fallback
- Viewer: resizable outline / spec / PDF panes, PDF.js overlay with exact
  line/figure/callout highlights at any zoom+rotation, source/display diff,
  ambiguity chooser, deep links
- Accessible by construction (keyboard-complete, non-canvas Callouts list)
