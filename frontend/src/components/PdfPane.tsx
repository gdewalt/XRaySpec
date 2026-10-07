import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  type ClipboardEvent as ReactClipboardEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { api } from "../api/client";
import type {
  CalloutDto,
  EntryDto,
  FigureOccurrenceDto,
  PdfAnnotationCreate,
  PdfAnnotationKind,
  PdfAnnotationRead,
} from "../api/types";
import { getToken } from "../auth/session";
import { normalizeCopiedText } from "../spec/text";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;

type PdfTool = "select" | PdfAnnotationKind | "delete";
type Point = [number, number];
type AnnotationDraft =
  | { kind: "highlight"; start: Point; end: Point }
  | { kind: "drawing"; points: Point[] };

const PDF_TOOLS: { id: PdfTool; label: string }[] = [
  { id: "select", label: "Select" },
  { id: "bookmark", label: "Bookmark" },
  { id: "note", label: "Note" },
  { id: "highlight", label: "Highlight" },
  { id: "drawing", label: "Draw" },
  { id: "delete", label: "Delete" },
];

const PDF_MARK_COLORS = [
  { label: "Yellow", value: "#ffe066" },
  { label: "Lime", value: "#a3e635" },
  { label: "Cyan", value: "#67e8f9" },
  { label: "Pink", value: "#f9a8d4" },
  { label: "Orange", value: "#fb923c" },
  { label: "Blue", value: "#2456d3" },
  { label: "Red", value: "#dc2626" },
  { label: "Black", value: "#1f2937" },
] as const;

function rotatePoint([x, y]: Point, rotation: number): Point {
  return rotation === 90
    ? [1 - y, x]
    : rotation === 180
      ? [1 - x, 1 - y]
      : rotation === 270
        ? [y, 1 - x]
        : [x, y];
}

function unrotatePoint([x, y]: Point, rotation: number): Point {
  return rotation === 90
    ? [y, 1 - x]
    : rotation === 180
      ? [1 - x, 1 - y]
      : rotation === 270
        ? [1 - y, x]
        : [x, y];
}

function geometryNumber(annotation: PdfAnnotationRead, key: string): number | null {
  const value = annotation.geometry[key];
  return typeof value === "number" ? value : null;
}

function drawingPoints(annotation: PdfAnnotationRead): Point[] {
  const points = annotation.geometry.points;
  if (!Array.isArray(points)) return [];
  return points.filter(
    (point): point is Point =>
      Array.isArray(point) &&
      point.length === 2 &&
      typeof point[0] === "number" &&
      typeof point[1] === "number",
  );
}

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

function SelectablePdfLine({
  entry,
  rotation,
  pageSize,
  highlighted,
  onSelect,
}: {
  entry: EntryDto;
  rotation: number;
  pageSize: { width: number; height: number };
  highlighted: boolean;
  onSelect: () => void;
}) {
  const boxRef = useRef<HTMLSpanElement>(null);
  const textRef = useRef<HTMLSpanElement>(null);
  const [horizontalScale, setHorizontalScale] = useState(1);
  const [x0, y0, x1, y1] = rotateBox(entry.box as number[], rotation);
  const lineHeight = Math.max(1, (y1 - y0) * pageSize.height);
  const fontSize = Math.max(1, lineHeight * 0.82);

  useLayoutEffect(() => {
    const boxWidth = boxRef.current?.clientWidth ?? 0;
    const naturalWidth = textRef.current?.scrollWidth ?? 0;
    if (!boxWidth || !naturalWidth) return;
    const nextScale = Math.max(0.2, Math.min(5, boxWidth / naturalWidth));
    setHorizontalScale((current) =>
      Math.abs(current - nextScale) > 0.002 ? nextScale : current,
    );
  }, [entry.source_text, fontSize, pageSize.width, rotation]);

  return (
    <span
      ref={boxRef}
      className={`pdf-line${highlighted ? " hit" : ""}`}
      data-ordinal={entry.ordinal}
      style={{
        left: `${x0 * 100}%`,
        top: `${y0 * 100}%`,
        width: `${(x1 - x0) * 100}%`,
        height: `${(y1 - y0) * 100}%`,
      }}
      onClick={onSelect}
    >
      <span
        ref={textRef}
        className="pdf-line-text"
        style={{
          fontSize: `${fontSize}px`,
          transform: `scaleX(${horizontalScale})`,
        }}
      >
        {entry.source_text}
      </span>
    </span>
  );
}

function ContinuousPdfPage({
  pdf,
  pageNumber,
  entries,
  callouts,
  figures,
  focusedFigure,
  annotations,
  tool,
  annotationColor,
  scale,
  rotation,
  highlightOrdinal,
  highlightCallouts,
  onSelectLine,
  onSelectCallout,
  onCreateAnnotation,
  onPlaceNote,
  onDeleteAnnotation,
  registerPage,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  entries: EntryDto[];
  callouts: CalloutDto[];
  figures: FigureOccurrenceDto[];
  focusedFigure?: FigureOccurrenceDto | null;
  annotations: PdfAnnotationRead[];
  tool: PdfTool;
  annotationColor: string;
  scale: number;
  rotation: number;
  highlightOrdinal: number | null;
  highlightCallouts?: Set<string>;
  onSelectLine: (entry: EntryDto) => void;
  onSelectCallout?: (callout: CalloutDto) => void;
  onCreateAnnotation: (annotation: PdfAnnotationCreate) => void;
  onPlaceNote: (pageIndex: number, point: Point) => void;
  onDeleteAnnotation: (annotation: PdfAnnotationRead) => void;
  registerPage: (page: number, element: HTMLDivElement | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderRef = useRef<RenderTask | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [draft, setDraft] = useState<AnnotationDraft | null>(null);

  useEffect(() => {
    let cancelled = false;
    void pdf.getPage(pageNumber).then(async (pdfPage) => {
      if (cancelled) return;
      const viewport = pdfPage.getViewport({ scale, rotation });
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2.5);
      const canvas = canvasRef.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context) return;
      canvas.width = Math.floor(viewport.width * pixelRatio);
      canvas.height = Math.floor(viewport.height * pixelRatio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      setSize({ width: viewport.width, height: viewport.height });
      renderRef.current?.cancel();
      renderRef.current = pdfPage.render({
        canvasContext: context,
        viewport,
        transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
      });
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

  function pointFromPointer(event: ReactPointerEvent<SVGSVGElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    const displayed: Point = [
      Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    ];
    return unrotatePoint(displayed, rotation);
  }

  function handleAnnotationPointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    if (tool === "select" || tool === "delete") return;
    event.preventDefault();
    const point = pointFromPointer(event);
    if (tool === "bookmark") {
      onCreateAnnotation({
        kind: "bookmark",
        page_index: pageNumber - 1,
        geometry: { x: point[0], y: point[1] },
        color: "#2456d3",
      });
      return;
    }
    if (tool === "note") {
      onPlaceNote(pageNumber - 1, point);
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    setDraft(
      tool === "highlight"
        ? { kind: "highlight", start: point, end: point }
        : { kind: "drawing", points: [point] },
    );
  }

  function handleAnnotationPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (!draft || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const point = pointFromPointer(event);
    setDraft((current) => {
      if (!current) return null;
      if (current.kind === "highlight") return { ...current, end: point };
      const last = current.points[current.points.length - 1];
      if (Math.hypot(point[0] - last[0], point[1] - last[1]) < 0.002) return current;
      return { ...current, points: [...current.points, point].slice(0, 2000) };
    });
  }

  function handleAnnotationPointerUp(event: ReactPointerEvent<SVGSVGElement>) {
    if (!draft) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (draft.kind === "highlight") {
      const x0 = Math.min(draft.start[0], draft.end[0]);
      const y0 = Math.min(draft.start[1], draft.end[1]);
      const x1 = Math.max(draft.start[0], draft.end[0]);
      const y1 = Math.max(draft.start[1], draft.end[1]);
      if (x1 - x0 > 0.004 && y1 - y0 > 0.004) {
        onCreateAnnotation({
          kind: "highlight",
          page_index: pageNumber - 1,
          geometry: { x0, y0, x1, y1 },
          color: annotationColor,
        });
      }
    } else if (draft.points.length > 1) {
      onCreateAnnotation({
        kind: "drawing",
        page_index: pageNumber - 1,
        geometry: { points: draft.points },
        color: annotationColor,
      });
    }
    setDraft(null);
  }

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
          {entries.map((entry) => (
            <SelectablePdfLine
              key={entry.entry_id}
              entry={entry}
              rotation={rotation}
              pageSize={size}
              highlighted={highlightOrdinal === entry.ordinal}
              onSelect={() => onSelectLine(entry)}
            />
          ))}
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
          {figures.map((figure, index) => {
            const [x0, y0, x1, y1] = rotateBox(figure.box, rotation);
            const hit =
              focusedFigure?.figure_id === figure.figure_id &&
              focusedFigure.page_index === figure.page_index &&
              focusedFigure.box.every((value, boxIndex) => value === figure.box[boxIndex]);
            return (
              <span
                key={`${figure.figure_id}-${index}`}
                className={`pdf-figure-target${hit ? " hit" : ""}`}
                data-figure-id={figure.figure_id}
                style={{
                  left: `${x0 * 100}%`,
                  top: `${y0 * 100}%`,
                  width: `${(x1 - x0) * 100}%`,
                  height: `${(y1 - y0) * 100}%`,
                }}
                aria-hidden="true"
              />
            );
          })}
        </div>
        <svg
          className={`pdf-annotation-layer tool-${tool}`}
          viewBox="0 0 1 1"
          preserveAspectRatio="none"
          aria-label="PDF annotations"
          onPointerDown={handleAnnotationPointerDown}
          onPointerMove={handleAnnotationPointerMove}
          onPointerUp={handleAnnotationPointerUp}
          onPointerCancel={() => setDraft(null)}
        >
          {annotations.map((annotation) => {
            const interactive =
              tool === "delete" ||
              (tool === "select" && (annotation.kind === "note" || annotation.kind === "bookmark"));
            const performAction = () => {
              if (tool === "delete") onDeleteAnnotation(annotation);
              else if (annotation.note) window.alert(annotation.note);
            };
            const activate = (event: ReactPointerEvent<SVGElement>) => {
              event.stopPropagation();
              if (interactive) performAction();
            };
            const onAnnotationKeyDown = (event: ReactKeyboardEvent<SVGElement>) => {
              if (interactive && (event.key === "Enter" || event.key === " ")) {
                event.preventDefault();
                performAction();
              }
            };
            if (annotation.kind === "highlight") {
              const box = ["x0", "y0", "x1", "y1"].map((key) =>
                geometryNumber(annotation, key),
              );
              if (box.some((value) => value === null)) return null;
              const [x0, y0, x1, y1] = rotateBox(box as number[], rotation);
              return (
                <rect
                  key={annotation.id}
                  className="pdf-saved-annotation pdf-highlight-mark"
                  x={x0}
                  y={y0}
                  width={x1 - x0}
                  height={y1 - y0}
                  fill={annotation.color ?? "#ffe066"}
                  onPointerDown={activate}
                  onKeyDown={onAnnotationKeyDown}
                  tabIndex={interactive ? 0 : undefined}
                  role={interactive ? "button" : undefined}
                  aria-label={interactive ? `${tool} highlight` : undefined}
                />
              );
            }
            if (annotation.kind === "drawing") {
              const points = drawingPoints(annotation).map((point) => rotatePoint(point, rotation));
              if (points.length < 2) return null;
              return (
                <polyline
                  key={annotation.id}
                  className="pdf-saved-annotation pdf-drawing-mark"
                  points={points.map((point) => point.join(",")).join(" ")}
                  fill="none"
                  stroke={annotation.color ?? "#2456d3"}
                  strokeWidth="0.004"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  onPointerDown={activate}
                  onKeyDown={onAnnotationKeyDown}
                  tabIndex={interactive ? 0 : undefined}
                  role={interactive ? "button" : undefined}
                  aria-label={interactive ? `${tool} drawing` : undefined}
                />
              );
            }
            const x = geometryNumber(annotation, "x");
            const y = geometryNumber(annotation, "y");
            if (x === null || y === null) return null;
            const point = rotatePoint([x, y], rotation);
            return (
              <g
                key={annotation.id}
                className={`pdf-saved-annotation pdf-${annotation.kind}-mark`}
                transform={`translate(${point[0]} ${point[1]})`}
                onPointerDown={activate}
                onKeyDown={onAnnotationKeyDown}
                tabIndex={interactive ? 0 : undefined}
                role={interactive ? "button" : undefined}
                aria-label={`${tool === "delete" ? "Delete" : "Open"} ${annotation.kind}`}
              >
                <circle r="0.018" fill={annotation.color ?? "#2456d3"} />
                <text
                  x="0"
                  y="0.006"
                  textAnchor="middle"
                  fontSize="0.021"
                  fontWeight="700"
                  fill="white"
                >
                  {annotation.kind === "note" ? "N" : "B"}
                </text>
                {annotation.note && <title>{annotation.note}</title>}
              </g>
            );
          })}
          {draft?.kind === "highlight" && (() => {
            const [x0, y0, x1, y1] = rotateBox(
              [
                Math.min(draft.start[0], draft.end[0]),
                Math.min(draft.start[1], draft.end[1]),
                Math.max(draft.start[0], draft.end[0]),
                Math.max(draft.start[1], draft.end[1]),
              ],
              rotation,
            );
            return (
              <rect
                className="pdf-highlight-mark draft"
                x={x0}
                y={y0}
                width={x1 - x0}
                height={y1 - y0}
                fill={annotationColor}
              />
            );
          })()}
          {draft?.kind === "drawing" && (
            <polyline
              className="pdf-drawing-mark draft"
              points={draft.points
                .map((point) => rotatePoint(point, rotation).join(","))
                .join(" ")}
              fill="none"
              stroke={annotationColor}
              strokeWidth="0.004"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}
        </svg>
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
  figures = [],
  focusedFigure,
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
  figures?: FigureOccurrenceDto[];
  focusedFigure?: FigureOccurrenceDto | null;
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
  const scrollFrameRef = useRef<number | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1.15);
  const [rotation, setRotation] = useState(0);
  const [tool, setTool] = useState<PdfTool>("select");
  const [highlightColor, setHighlightColor] = useState("#ffe066");
  const [drawingColor, setDrawingColor] = useState("#2456d3");
  const [annotations, setAnnotations] = useState<PdfAnnotationRead[]>([]);
  const [pendingNote, setPendingNote] = useState<{ pageIndex: number; point: Point } | null>(null);
  const [noteDraft, setNoteDraft] = useState("");

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
    let cancelled = false;
    void api.listPdfAnnotations(documentId).then(
      (items) => {
        if (!cancelled) setAnnotations(items);
      },
      (reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  // Only explicit page navigation moves the scroll position. During ordinary
  // scrolling, handleScroll updates visiblePageRef before notifying the parent,
  // so the resulting page prop cannot fight the scrollbar with scrollIntoView.
  useEffect(() => {
    if (!pdf || page === visiblePageRef.current) return;
    const pageElement = pageRefs.current.get(page);
    if (!pageElement) return;
    visiblePageRef.current = page;
    const frame = window.requestAnimationFrame(() => {
      pageElement.scrollIntoView({ block: "start" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [page, pdf]);

  // Figure-reference clicks remain intentional navigation and may center a
  // target even when it lives on the page that is already visible.
  useEffect(() => {
    if (!pdf || !focusedFigure) return;
    const figurePage = focusedFigure.page_index + 1;
    const pageElement = pageRefs.current.get(figurePage);
    if (!pageElement) return;
    visiblePageRef.current = figurePage;
    const frame = window.requestAnimationFrame(() => {
      const figureTarget = Array.from(
        pageElement.querySelectorAll<HTMLElement>(".pdf-figure-target"),
      ).find((element) => element.dataset.figureId === focusedFigure.figure_id);
      (figureTarget ?? pageElement).scrollIntoView({
        block: figureTarget ? "center" : "start",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [pdf, focusedFigure]);

  useEffect(
    () => () => {
      if (scrollFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollFrameRef.current);
      }
    },
    [],
  );

  function registerPage(pageNumber: number, element: HTMLDivElement | null) {
    if (element) pageRefs.current.set(pageNumber, element);
    else pageRefs.current.delete(pageNumber);
  }

  function handleScroll() {
    if (scrollFrameRef.current !== null) return;
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      updateVisiblePage();
    });
  }

  function updateVisiblePage() {
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
    const text = normalizeCopiedText(nativeSelection.toString());
    if (text) onSelectionText?.(text);
    const covered = Array.from(container.querySelectorAll<HTMLElement>(".pdf-line"))
      .filter((line) => range.intersectsNode(line))
      .map((line) => Number(line.dataset.ordinal))
      .filter((ordinal) => !Number.isNaN(ordinal));
    if (covered.length > 0) onSelectRange(Math.min(...covered), Math.max(...covered));
  }

  function handleCopy(event: ReactClipboardEvent<HTMLDivElement>) {
    const nativeSelection = window.getSelection();
    const container = scrollRef.current;
    if (!nativeSelection || nativeSelection.isCollapsed || !container) return;
    const range = nativeSelection.getRangeAt(0);
    if (!container.contains(range.commonAncestorContainer)) return;
    const text = normalizeCopiedText(nativeSelection.toString());
    if (!text) return;
    event.preventDefault();
    event.clipboardData.setData("text/plain", text);
    onSelectionText?.(text);
  }

  async function createAnnotation(annotation: PdfAnnotationCreate) {
    try {
      const created = await api.createPdfAnnotation(documentId, annotation);
      setAnnotations((items) => [...items, created]);
      if (annotation.kind === "bookmark" || annotation.kind === "note") setTool("select");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function deleteAnnotation(annotation: PdfAnnotationRead) {
    try {
      await api.deletePdfAnnotation(annotation.id);
      setAnnotations((items) => items.filter((item) => item.id !== annotation.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function placeNote(pageIndex: number, point: Point) {
    setPendingNote({ pageIndex, point });
    setNoteDraft("");
  }

  function saveNote() {
    const note = noteDraft.trim();
    if (!pendingNote || !note) return;
    void createAnnotation({
      kind: "note",
      page_index: pendingNote.pageIndex,
      geometry: { x: pendingNote.point[0], y: pendingNote.point[1] },
      color: "#b45309",
      note,
    });
    setPendingNote(null);
    setNoteDraft("");
  }

  const pageCount = pdf?.numPages ?? 0;
  const activeMarkColor = tool === "drawing" ? drawingColor : highlightColor;

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
        <div className="pdf-annotation-tools" role="toolbar" aria-label="PDF annotation tools">
          {PDF_TOOLS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`secondary${tool === item.id ? " active" : ""}${item.id === "delete" ? " danger-icon" : ""}`}
              aria-pressed={tool === item.id}
              onClick={() => setTool(item.id)}
            >
              {item.label}
            </button>
          ))}
          {(tool === "highlight" || tool === "drawing") && (
            <label className="pdf-color-control">
              <span>Color</span>
              <select
                value={activeMarkColor}
                aria-label={`${tool === "drawing" ? "Drawing" : "Highlight"} color`}
                onChange={(event) => {
                  if (tool === "drawing") setDrawingColor(event.target.value);
                  else setHighlightColor(event.target.value);
                }}
              >
                {PDF_MARK_COLORS.map((color) => (
                  <option key={color.value} value={color.value}>
                    {color.label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>

      {pendingNote && (
        <div className="pdf-note-editor" role="dialog" aria-label="Add PDF note">
          <label htmlFor="pdf-note-text">Note</label>
          <textarea
            id="pdf-note-text"
            value={noteDraft}
            onChange={(event) => setNoteDraft(event.target.value)}
            rows={3}
            maxLength={4000}
            autoFocus
          />
          <button type="button" onClick={saveNote} disabled={!noteDraft.trim()}>
            Save note
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => setPendingNote(null)}
          >
            Cancel
          </button>
        </div>
      )}

      {error && (
        <p className="status err" role="alert">
          {error}
        </p>
      )}

      <div
        ref={scrollRef}
        className="pdf-scroll"
        onScroll={handleScroll}
        onMouseUp={handleMouseUp}
        onCopy={handleCopy}
      >
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
                  figures={figures.filter(
                    (figure) => figure.page_index === index && figure.box?.length === 4,
                  )}
                  focusedFigure={focusedFigure}
                  annotations={annotations.filter(
                    (annotation) => annotation.page_index === index,
                  )}
                  tool={tool}
                  annotationColor={activeMarkColor}
                  scale={scale}
                  rotation={rotation}
                  highlightOrdinal={highlightOrdinal}
                  highlightCallouts={highlightCallouts}
                  onSelectLine={onSelectLine}
                  onSelectCallout={onSelectCallout}
                  onCreateAnnotation={(annotation) => void createAnnotation(annotation)}
                  onPlaceNote={placeNote}
                  onDeleteAnnotation={(annotation) => void deleteAnnotation(annotation)}
                  registerPage={registerPage}
                />
              );
            })}
        </div>
      </div>
    </div>
  );
}
