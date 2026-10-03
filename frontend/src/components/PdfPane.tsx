import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";

import type { CalloutDto, EntryDto } from "../api/types";
import { getToken } from "../auth/session";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;

/** Rotate a normalized [x0,y0,x1,y1] box by 0/90/180/270° to match the viewport. */
function rotateBox(box: number[], rotation: number): [number, number, number, number] {
  const rot = ([x, y]: number[]): [number, number] =>
    rotation === 90
      ? [1 - y, x]
      : rotation === 180
        ? [1 - x, 1 - y]
        : rotation === 270
          ? [y, 1 - x]
          : [x, y];
  const [ax, ay] = rot([box[0], box[1]]);
  const [bx, by] = rot([box[2], box[3]]);
  return [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)];
}

function ContinuousPdfPage({
  pdf,
  pageNumber,
  entries,
  callouts,
  scale,
  rotation,
  highlightOrdinal,
  highlightCallouts,
  onSelectLine,
  onSelectCallout,
  registerPage,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  entries: EntryDto[];
  callouts: CalloutDto[];
  scale: number;
  rotation: number;
  highlightOrdinal: number | null;
  highlightCallouts?: Set<string>;
  onSelectLine: (entry: EntryDto) => void;
  onSelectCallout?: (callout: CalloutDto) => void;
  registerPage: (page: number, element: HTMLDivElement | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderRef = useRef<RenderTask | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    let cancelled = false;
    void pdf.getPage(pageNumber).then(async (pdfPage) => {
      if (cancelled) return;
      const viewport = pdfPage.getViewport({ scale, rotation });
      const canvas = canvasRef.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context) return;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      setSize({ width: viewport.width, height: viewport.height });
      renderRef.current?.cancel();
      renderRef.current = pdfPage.render({ canvasContext: context, viewport });
      try {
        await renderRef.current.promise;
      } catch {
        // A newer scale or rotation may cancel the in-flight render.
      }
    });
    return () => {
      cancelled = true;
      renderRef.current?.cancel();
    };
  }, [pdf, pageNumber, scale, rotation]);

  return (
    <div
      ref={(element) => registerPage(pageNumber, element)}
      className="pdf-page-wrap"
      data-page={pageNumber}
      aria-label={`PDF page ${pageNumber}`}
    >
      <div
        className="pdf-page"
        style={{ width: size.width || undefined, height: size.height || undefined }}
      >
        <canvas ref={canvasRef} />
        <div className="pdf-overlay">
          {entries.map((entry) => {
            const [x0, y0, x1, y1] = rotateBox(entry.box as number[], rotation);
            return (
              <span
                key={entry.entry_id}
                className={`pdf-line${highlightOrdinal === entry.ordinal ? " hit" : ""}`}
                data-ordinal={entry.ordinal}
                style={{
                  left: `${x0 * 100}%`,
                  top: `${y0 * 100}%`,
                  width: `${(x1 - x0) * 100}%`,
                  height: `${(y1 - y0) * 100}%`,
                }}
                onClick={() => onSelectLine(entry)}
              >
                {entry.source_text}
              </span>
            );
          })}
          {callouts.map((callout) => {
            const [x0, y0, x1, y1] = rotateBox(callout.box, rotation);
            const hit = highlightCallouts?.has(callout.callout_id);
            return (
              <button
                key={callout.callout_id}
                type="button"
                className={`pdf-callout${hit ? " hit" : ""}`}
                style={{
                  left: `${x0 * 100}%`,
                  top: `${y0 * 100}%`,
                  width: `${(x1 - x0) * 100}%`,
                  height: `${(y1 - y0) * 100}%`,
                }}
                title={`Callout ${callout.value}${callout.figure_id ? ` (FIG. ${callout.figure_id})` : ""}`}
                onClick={() => onSelectCallout?.(callout)}
              >
                <span className="pdf-callout-tag">{callout.value}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/**
 * Continuous PDF pane. Every page is rendered in one scrollable surface while
 * preserving selectable extracted text and drawing-callout navigation.
 */
export function PdfPane({
  documentId,
  entries,
  callouts = [],
  page,
  onPageChange,
  highlightOrdinal,
  highlightCallouts,
  onSelectLine,
  onSelectRange,
  onSelectCallout,
  onActivate,
  onSelectionText,
}: {
  documentId: string;
  entries: EntryDto[];
  callouts?: CalloutDto[];
  page: number;
  onPageChange: (page: number) => void;
  highlightOrdinal: number | null;
  highlightCallouts?: Set<string>;
  onSelectLine: (entry: EntryDto) => void;
  onSelectRange: (startOrdinal: number, endOrdinal: number) => void;
  onSelectCallout?: (callout: CalloutDto) => void;
  onActivate?: () => void;
  onSelectionText?: (text: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());
  const visiblePageRef = useRef(1);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1.15);
  const [rotation, setRotation] = useState(0);

  useEffect(() => {
    let loadedDocument: PDFDocumentProxy | null = null;
    let cancelled = false;
    setError(null);
    const task = pdfjsLib.getDocument({
      url: `/api/v1/documents/${documentId}/source.pdf`,
      httpHeaders: { Authorization: `Bearer ${getToken() ?? ""}` },
    });
    task.promise.then(
      (document) => {
        if (cancelled) {
          void document.destroy();
          return;
        }
        loadedDocument = document;
        setPdf(document);
      },
      (reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => {
      cancelled = true;
      void task.destroy();
      void loadedDocument?.destroy();
    };
  }, [documentId]);

  useEffect(() => {
    if (!pdf || page === visiblePageRef.current) return;
    pageRefs.current.get(page)?.scrollIntoView({ block: "start" });
  }, [page, pdf]);

  function registerPage(pageNumber: number, element: HTMLDivElement | null) {
    if (element) pageRefs.current.set(pageNumber, element);
    else pageRefs.current.delete(pageNumber);
  }

  function handleScroll() {
    const container = scrollRef.current;
    if (!container) return;
    const containerTop = container.getBoundingClientRect().top;
    let nearestPage = visiblePageRef.current;
    let nearestDistance = Number.POSITIVE_INFINITY;
    for (const [pageNumber, element] of pageRefs.current) {
      const distance = Math.abs(element.getBoundingClientRect().top - containerTop - 8);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestPage = pageNumber;
      }
    }
    if (nearestPage !== visiblePageRef.current) {
      visiblePageRef.current = nearestPage;
      onPageChange(nearestPage);
    }
  }

  function handleMouseUp() {
    const nativeSelection = window.getSelection();
    const container = scrollRef.current;
    if (!nativeSelection || nativeSelection.isCollapsed || !container) return;
    const range = nativeSelection.getRangeAt(0);
    if (!container.contains(range.commonAncestorContainer)) return;
    const text = nativeSelection.toString().trim();
    if (text) onSelectionText?.(text);
    const covered = Array.from(container.querySelectorAll<HTMLElement>(".pdf-line"))
      .filter((line) => range.intersectsNode(line))
      .map((line) => Number(line.dataset.ordinal))
      .filter((ordinal) => !Number.isNaN(ordinal));
    if (covered.length > 0) onSelectRange(Math.min(...covered), Math.max(...covered));
  }

  const pageCount = pdf?.numPages ?? 0;

  return (
    <div className="pdf-pane" onPointerDown={onActivate}>
      <div className="pdf-controls">
        <span className="pdf-pageno">
          Page {page} of {pageCount || "…"}
        </span>
        <button
          type="button"
          className="secondary"
          onClick={() => setScale((value) => Math.max(0.5, value - 0.15))}
          aria-label="Zoom out"
        >
          −
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => setScale((value) => Math.min(3, value + 0.15))}
          aria-label="Zoom in"
        >
          +
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => setRotation((value) => (value + 90) % 360)}
          title="Rotate 90°"
          aria-label="Rotate PDF 90 degrees"
        >
          ⟳
        </button>
        <span className="pdf-scroll-hint">Scroll to move between pages</span>
      </div>

      {error && (
        <p className="status err" role="alert">
          {error}
        </p>
      )}

      <div ref={scrollRef} className="pdf-scroll" onScroll={handleScroll} onMouseUp={handleMouseUp}>
        <div className="pdf-pages">
          {pdf &&
            Array.from({ length: pdf.numPages }, (_, index) => {
              const pageNumber = index + 1;
              return (
                <ContinuousPdfPage
                  key={pageNumber}
                  pdf={pdf}
                  pageNumber={pageNumber}
                  entries={entries.filter(
                    (entry) => entry.page_index === index && entry.box?.length === 4,
                  )}
                  callouts={callouts.filter(
                    (callout) => callout.page_index === index && callout.box?.length === 4,
                  )}
                  scale={scale}
                  rotation={rotation}
                  highlightOrdinal={highlightOrdinal}
                  highlightCallouts={highlightCallouts}
                  onSelectLine={onSelectLine}
                  onSelectCallout={onSelectCallout}
                  registerPage={registerPage}
                />
              );
            })}
        </div>
      </div>
    </div>
  );
}
