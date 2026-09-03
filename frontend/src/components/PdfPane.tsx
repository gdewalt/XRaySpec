import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";

import { getToken } from "../auth/session";
import type { EntryDto } from "../api/types";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;

/**
 * PDF pane (DESIGN.md §16.4): renders a page with PDF.js and overlays a
 * selectable text layer positioned from each line's normalized box — so text can
 * be selected on the page (even for scanned patents, whose text comes from the
 * artifact) and copied with a citation. Clicking a line selects it; dragging a
 * selection resolves to the covered lines.
 */
export function PdfPane({
  documentId,
  entries,
  page,
  onPageChange,
  highlightOrdinal,
  onSelectLine,
  onSelectRange,
}: {
  documentId: string;
  entries: EntryDto[];
  page: number;
  onPageChange: (p: number) => void;
  highlightOrdinal: number | null;
  onSelectLine: (e: EntryDto) => void;
  onSelectRange: (startOrdinal: number, endOrdinal: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const renderRef = useRef<RenderTask | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1.3);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    let doc: PDFDocumentProxy | null = null;
    const task = pdfjsLib.getDocument({
      url: `/api/v1/documents/${documentId}/source.pdf`,
      httpHeaders: { Authorization: `Bearer ${getToken() ?? ""}` },
    });
    task.promise.then(
      (d) => {
        doc = d;
        setPdf(d);
      },
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      task.destroy();
      doc?.destroy();
    };
  }, [documentId]);

  useEffect(() => {
    if (!pdf) return;
    let cancelled = false;
    pdf.getPage(page).then(async (pg) => {
      if (cancelled) return;
      const viewport = pg.getViewport({ scale });
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      setSize({ w: viewport.width, h: viewport.height });
      renderRef.current?.cancel();
      renderRef.current = pg.render({ canvasContext: ctx, viewport });
      try {
        await renderRef.current.promise;
      } catch {
        /* render cancelled */
      }
    });
    return () => {
      cancelled = true;
    };
  }, [pdf, page, scale]);

  function handleMouseUp() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !overlayRef.current) return;
    const range = sel.getRangeAt(0);
    const covered = Array.from(overlayRef.current.querySelectorAll<HTMLElement>(".pdf-line"))
      .filter((s) => range.intersectsNode(s))
      .map((s) => Number(s.dataset.ordinal))
      .filter((n) => !Number.isNaN(n));
    if (covered.length) onSelectRange(Math.min(...covered), Math.max(...covered));
  }

  const pageEntries = entries.filter((e) => e.page_index === page - 1 && e.box);
  const pageCount = pdf?.numPages ?? 0;

  return (
    <div className="pdf-pane">
      <div className="pdf-controls">
        <button type="button" className="secondary" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          ‹
        </button>
        <span className="pdf-pageno">
          {page} / {pageCount || "…"}
        </span>
        <button
          type="button"
          className="secondary"
          disabled={pageCount > 0 && page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          ›
        </button>
        <button type="button" className="secondary" onClick={() => setScale((s) => Math.max(0.5, s - 0.2))}>
          −
        </button>
        <button type="button" className="secondary" onClick={() => setScale((s) => Math.min(3, s + 0.2))}>
          +
        </button>
      </div>

      {error && (
        <p className="status err" role="alert">
          {error}
        </p>
      )}

      <div className="pdf-scroll">
        <div className="pdf-page" style={{ width: size.w || undefined, height: size.h || undefined }}>
          <canvas ref={canvasRef} />
          <div ref={overlayRef} className="pdf-overlay" onMouseUp={handleMouseUp}>
            {pageEntries.map((e) => {
              const [x0, y0, x1, y1] = e.box as number[];
              return (
                <span
                  key={e.entry_id}
                  className={`pdf-line${highlightOrdinal === e.ordinal ? " hit" : ""}`}
                  data-ordinal={e.ordinal}
                  style={{
                    left: `${x0 * 100}%`,
                    top: `${y0 * 100}%`,
                    width: `${(x1 - x0) * 100}%`,
                    height: `${(y1 - y0) * 100}%`,
                  }}
                  onClick={() => onSelectLine(e)}
                >
                  {e.source_text}
                </span>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
