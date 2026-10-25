import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  type ClipboardEvent as ReactClipboardEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
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
  PdfAnnotationUpdate,
} from "../api/types";
import { getToken } from "../auth/session";
import { formatSelectionWithCitation, normalizeCopiedText } from "../spec/text";
import { Icon, type IconName } from "./Icon";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;

type PdfTool = "select" | Exclude<PdfAnnotationKind, "bookmark">;
type Point = [number, number];
type TextRange = { entry_id: string; start: number; end: number; quote: string };
export type PdfTextHighlightRange = { start: number; end: number; color: string };
type AnnotationDraft =
  | { kind: "highlight"; start: Point; end: Point }
  | { kind: "drawing"; points: Point[] };
type AnnotationHistory =
  | {
      kind: "create";
      snapshot: PdfAnnotationRead;
      currentId: string | null;
    }
  | {
      kind: "delete";
      snapshot: PdfAnnotationRead;
      currentId: string | null;
    }
  | {
      kind: "update";
      before: PdfAnnotationRead;
      after: PdfAnnotationRead;
    };

const PDF_TOOLS: { id: PdfTool; label: string; icon: IconName }[] = [
  { id: "select", label: "Select text", icon: "mouse-pointer" },
  { id: "note", label: "Add note", icon: "message-square" },
  { id: "highlight", label: "Highlight", icon: "highlighter" },
  { id: "drawing", label: "Draw", icon: "pencil" },
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

const PDF_SCALE_MIN = 0.5;
const PDF_SCALE_MAX = 3;
const PDF_SCALE_STEP = 0.15;

export function adjustedPdfScale(scale: number, direction: -1 | 1): number {
  const next = Math.round((scale + direction * PDF_SCALE_STEP) * 100) / 100;
  return Math.max(PDF_SCALE_MIN, Math.min(PDF_SCALE_MAX, next));
}

export function simplifyDrawingPoints(points: Point[], tolerance = 0.0015): Point[] {
  if (points.length <= 2) return points;
  const simplified: Point[] = [points[0]];
  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = simplified[simplified.length - 1];
    const point = points[index];
    if (Math.hypot(point[0] - previous[0], point[1] - previous[1]) >= tolerance) {
      simplified.push(point);
    }
  }
  const last = points[points.length - 1];
  if (last !== simplified[simplified.length - 1]) simplified.push(last);
  return simplified;
}

export function removeAnnotationById(
  annotations: PdfAnnotationRead[],
  annotationId: string,
): PdfAnnotationRead[] {
  return annotations.filter((annotation) => annotation.id !== annotationId);
}

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

function storedHighlightRects(annotation: PdfAnnotationRead): number[][] {
  const rects = annotation.geometry.rects;
  if (Array.isArray(rects)) {
    return rects.filter(
      (rect): rect is number[] =>
        Array.isArray(rect) && rect.length === 4 && rect.every((value) => typeof value === "number"),
    );
  }
  const box = ["x0", "y0", "x1", "y1"].map((key) => geometryNumber(annotation, key));
  return box.some((value) => value === null) ? [] : [box as number[]];
}

function textHighlightRanges(
  annotations: PdfAnnotationRead[],
): Map<string, PdfTextHighlightRange[]> {
  const highlights = new Map<string, PdfTextHighlightRange[]>();
  for (const annotation of annotations) {
    if (annotation.kind !== "highlight") continue;
    const color = annotation.color ?? "#ffe066";
    const ranges = annotation.geometry.text_ranges;
    if (Array.isArray(ranges)) {
      for (const range of ranges) {
        if (
          typeof range === "object" &&
          range !== null &&
          typeof range.entry_id === "string" &&
          typeof range.start === "number" &&
          typeof range.end === "number"
        ) {
          const current = highlights.get(range.entry_id) ?? [];
          current.push({ start: range.start, end: range.end, color });
          highlights.set(range.entry_id, current);
        }
      }
      continue;
    }
    const entryIds = annotation.geometry.entry_ids;
    if (!Array.isArray(entryIds)) continue;
    for (const entryId of entryIds) {
      if (typeof entryId !== "string") continue;
      const current = highlights.get(entryId) ?? [];
      current.push({ start: 0, end: Number.MAX_SAFE_INTEGER, color });
      highlights.set(entryId, current);
    }
  }
  return highlights;
}

function groupByPage<T extends { page_index: number }>(items: T[]): Map<number, T[]> {
  const grouped = new Map<number, T[]>();
  for (const item of items) {
    const pageItems = grouped.get(item.page_index);
    if (pageItems) pageItems.push(item);
    else grouped.set(item.page_index, [item]);
  }
  return grouped;
}

function annotationCreatePayload(annotation: PdfAnnotationRead): PdfAnnotationCreate {
  return {
    kind: annotation.kind,
    page_index: annotation.page_index,
    geometry: annotation.geometry,
    color: annotation.color,
    note: annotation.note,
  };
}

function StickyPdfNote({
  annotation,
  rotation,
  pageElement,
  active,
  tool,
  onActivate,
  onUpdate,
}: {
  annotation: PdfAnnotationRead;
  rotation: number;
  pageElement: HTMLDivElement | null;
  active: boolean;
  tool: PdfTool;
  onActivate: () => void;
  onUpdate: (annotation: PdfAnnotationRead, update: PdfAnnotationUpdate) => void;
}) {
  const savedX = geometryNumber(annotation, "x") ?? 0;
  const savedY = geometryNumber(annotation, "y") ?? 0;
  const savedWidth = geometryNumber(annotation, "width") ?? 0.28;
  const savedHeight = geometryNumber(annotation, "height") ?? 0.2;
  const [position, setPosition] = useState<Point>([savedX, savedY]);
  const [size, setSize] = useState<Point>([savedWidth, savedHeight]);
  const [draft, setDraft] = useState(annotation.note ?? "");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const saveTimerRef = useRef<number | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    offset: Point;
    current: Point;
  } | null>(null);
  const resizeRef = useRef<{
    pointerId: number;
    origin: Point;
    initial: Point;
    current: Point;
  } | null>(null);
  const collapsed = annotation.geometry.collapsed === true;

  useEffect(() => setDraft(annotation.note ?? ""), [annotation.note]);
  useEffect(() => setPosition([savedX, savedY]), [savedX, savedY]);
  useEffect(() => setSize([savedWidth, savedHeight]), [savedWidth, savedHeight]);
  useEffect(() => {
    if (active && !collapsed) textareaRef.current?.focus();
  }, [active, collapsed]);
  useEffect(
    () => () => {
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    },
    [],
  );

  function pointFromPage(event: ReactPointerEvent<HTMLElement>): Point | null {
    if (!pageElement) return null;
    const rect = pageElement.getBoundingClientRect();
    const displayed: Point = [
      Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    ];
    return unrotatePoint(displayed, rotation);
  }

  function beginDrag(event: ReactPointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    if (tool !== "select") return;
    onActivate();
    const point = pointFromPage(event);
    if (!point) return;
    dragRef.current = {
      pointerId: event.pointerId,
      offset: [point[0] - position[0], point[1] - position[1]],
      current: position,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const point = pointFromPage(event);
    if (!point) return;
    const next: Point = [
      Math.max(0.01, Math.min(0.99 - (collapsed ? 0.12 : size[0]), point[0] - drag.offset[0])),
      Math.max(0.01, Math.min(0.99 - (collapsed ? 0.05 : size[1]), point[1] - drag.offset[1])),
    ];
    drag.current = next;
    setPosition(next);
  }

  function beginResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    const point = pointFromPage(event);
    if (!point) return;
    resizeRef.current = {
      pointerId: event.pointerId,
      origin: point,
      initial: size,
      current: size,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveResize(event: ReactPointerEvent<HTMLDivElement>) {
    const resize = resizeRef.current;
    if (!resize || resize.pointerId !== event.pointerId) return;
    const point = pointFromPage(event);
    if (!point) return;
    const next: Point = [
      Math.max(0.18, Math.min(0.6, resize.initial[0] + point[0] - resize.origin[0])),
      Math.max(0.12, Math.min(0.6, resize.initial[1] + point[1] - resize.origin[1])),
    ];
    resize.current = next;
    setSize(next);
  }

  function finishResize(event: ReactPointerEvent<HTMLDivElement>) {
    const resize = resizeRef.current;
    if (!resize || resize.pointerId !== event.pointerId) return;
    resizeRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    onUpdate(annotation, {
      geometry: {
        ...annotation.geometry,
        width: resize.current[0],
        height: resize.current[1],
      },
    });
  }

  function finishDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const finalPosition = drag.current;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    onUpdate(annotation, {
      geometry: {
        ...annotation.geometry,
        x: finalPosition[0],
        y: finalPosition[1],
      },
    });
  }

  function updateText(value: string) {
    setDraft(value);
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      onUpdate(annotation, { note: value });
    }, 500);
  }

  function flushText() {
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = null;
    if (draft !== (annotation.note ?? "")) onUpdate(annotation, { note: draft });
  }

  const displayed = rotatePoint(position, rotation);
  return (
    <section
      className={`pdf-sticky-note${collapsed ? " collapsed" : ""}${active ? " active" : ""}`}
      style={{
        left: `${displayed[0] * 100}%`,
        top: `${displayed[1] * 100}%`,
        width: collapsed ? undefined : `${size[0] * 100}%`,
        height: collapsed ? undefined : `${size[1] * 100}%`,
      }}
      aria-label="PDF note"
      onPointerDown={(event) => {
        event.stopPropagation();
        onActivate();
      }}
    >
      <div
        className="pdf-sticky-note-head"
        onPointerDown={beginDrag}
        onPointerMove={moveDrag}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
      >
        <Icon name="message-square" size={15} />
        <span>Note</span>
        <button
          type="button"
          className="pdf-sticky-collapse"
          aria-label={collapsed ? "Expand note" : "Collapse note"}
          title={collapsed ? "Expand note" : "Collapse note"}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            onUpdate(annotation, {
              geometry: { ...annotation.geometry, collapsed: !collapsed },
            });
          }}
        >
          <Icon name={collapsed ? "chevron-down" : "chevron-up"} size={14} />
        </button>
      </div>
      {!collapsed && (
        <>
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(event) => updateText(event.target.value)}
            onBlur={flushText}
            onPointerDown={(event) => event.stopPropagation()}
            maxLength={4000}
            placeholder="Type a note…"
            aria-label="Note text"
          />
          <div
            className="pdf-sticky-resize"
            role="separator"
            aria-label="Resize note"
            onPointerDown={beginResize}
            onPointerMove={moveResize}
            onPointerUp={finishResize}
            onPointerCancel={finishResize}
          />
        </>
      )}
    </section>
  );
}

export function lineHighlightGeometry(start: Point, end: Point, entries: EntryDto[]) {
  const fallbackGeometry = () => {
    let x0 = Math.min(start[0], end[0]);
    let x1 = Math.max(start[0], end[0]);
    let y0 = Math.min(start[1], end[1]);
    let y1 = Math.max(start[1], end[1]);
    if (x1 - x0 < 0.006) {
      x0 = Math.max(0, x0 - 0.006);
      x1 = Math.min(1, x1 + 0.006);
    }
    if (y1 - y0 < 0.008) {
      y0 = Math.max(0, y0 - 0.009);
      y1 = Math.min(1, y1 + 0.009);
    }
    return x1 - x0 > 0.002
      ? { rects: [[x0, y0, x1, y1]], entryIds: [] as string[], textRanges: [] as TextRange[] }
      : { rects: [] as number[][], entryIds: [] as string[], textRanges: [] as TextRange[] };
  };
  const lines = entries
    .filter((entry) => entry.box?.length === 4)
    .sort((a, b) => a.ordinal - b.ordinal);
  const distanceToEntry = (point: Point, entry: EntryDto) => {
    const box = entry.box!;
    const dx = Math.max(box[0] - point[0], 0, point[0] - box[2]);
    const dy = Math.max(box[1] - point[1], 0, point[1] - box[3]);
    return Math.hypot(dx, dy);
  };
  const nearestEntry = (point: Point) => {
    let bestIndex = -1;
    let bestDistance = Number.POSITIVE_INFINITY;
    lines.forEach((entry, index) => {
      const distance = distanceToEntry(point, entry);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    });
    return { index: bestIndex, distance: bestDistance };
  };
  const startEntry = nearestEntry(start);
  const endEntry = nearestEntry(end);
  if (
    startEntry.index < 0 ||
    endEntry.index < 0 ||
    startEntry.distance > 0.05 ||
    endEntry.distance > 0.05
  ) {
    return fallbackGeometry();
  }
  const startIndex = startEntry.index;
  const endIndex = endEntry.index;
  const forward = startIndex <= endIndex;
  const firstIndex = Math.min(startIndex, endIndex);
  const lastIndex = Math.max(startIndex, endIndex);
  const first = forward ? start : end;
  const last = forward ? end : start;
  const selectedLines = lines.slice(firstIndex, lastIndex + 1);
  const textRanges: TextRange[] = [];
  const rects = selectedLines.flatMap((entry, index) => {
    const box = entry.box;
    if (!box) return [];
    let left = box[0];
    let right = box[2];
    if (selectedLines.length === 1) {
      left = Math.max(left, Math.min(first[0], last[0]));
      right = Math.min(right, Math.max(first[0], last[0]));
    } else if (index === 0) {
      left = Math.max(left, first[0]);
    } else if (index === selectedLines.length - 1) {
      right = Math.min(right, last[0]);
    }
    const text = entry.display_text || entry.source_text;
    const width = Math.max(0.0001, box[2] - box[0]);
    const offsetForX = (x: number) =>
      Math.max(0, Math.min(text.length, Math.round(((x - box[0]) / width) * text.length)));
    let startOffset = 0;
    let endOffset = text.length;
    if (selectedLines.length === 1) {
      startOffset = offsetForX(left);
      endOffset = offsetForX(right);
    } else if (index === 0) {
      startOffset = offsetForX(left);
    } else if (index === selectedLines.length - 1) {
      endOffset = offsetForX(right);
    }
    if (endOffset > startOffset) {
      textRanges.push({
        entry_id: entry.entry_id,
        start: startOffset,
        end: endOffset,
        quote: text.slice(startOffset, endOffset),
      });
    }
    const snappedLeft = text.length ? box[0] + (startOffset / text.length) * width : left;
    const snappedRight = text.length ? box[0] + (endOffset / text.length) * width : right;
    const verticalInset = Math.min((box[3] - box[1]) * 0.1, 0.003);
    return snappedRight - snappedLeft > 0.002
      ? [[snappedLeft, box[1] + verticalInset, snappedRight, box[3] - verticalInset]]
      : [];
  });
  return { rects, entryIds: selectedLines.map((entry) => entry.entry_id), textRanges };
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
  }, [entry.display_text, fontSize, pageSize.width, rotation]);

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
        {entry.display_text}
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
  annotationOpacity,
  drawingStrokeWidth,
  scale,
  rotation,
  highlightOrdinal,
  highlightCallouts,
  onSelectLine,
  onSelectCallout,
  onCreateAnnotation,
  onUpdateAnnotation,
  selectedAnnotationId,
  onSelectAnnotation,
  activeNoteId,
  onActivateNote,
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
  annotationOpacity: number;
  drawingStrokeWidth: number;
  scale: number;
  rotation: number;
  highlightOrdinal: number | null;
  highlightCallouts?: Set<string>;
  onSelectLine: (entry: EntryDto) => void;
  onSelectCallout?: (callout: CalloutDto) => void;
  onCreateAnnotation: (annotation: PdfAnnotationCreate) => void;
  onUpdateAnnotation: (annotation: PdfAnnotationRead, update: PdfAnnotationUpdate) => void;
  selectedAnnotationId: string | null;
  onSelectAnnotation: (annotationId: string | null) => void;
  activeNoteId: string | null;
  onActivateNote: (annotationId: string) => void;
  registerPage: (page: number, element: HTMLDivElement | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pageElementRef = useRef<HTMLDivElement>(null);
  const renderRef = useRef<RenderTask | null>(null);
  const draftRef = useRef<AnnotationDraft | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [draft, setDraft] = useState<AnnotationDraft | null>(null);

  function updateDraft(next: AnnotationDraft | null) {
    draftRef.current = next;
    setDraft(next);
  }

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
    if (tool === "select") {
      onSelectAnnotation(null);
      return;
    }
    event.preventDefault();
    const point = pointFromPointer(event);
    if (tool === "note") {
      onCreateAnnotation({
        kind: "note",
        page_index: pageNumber - 1,
        geometry: {
          version: 2,
          x: Math.max(0.01, Math.min(0.78, point[0])),
          y: Math.max(0.01, Math.min(0.82, point[1])),
          width: 0.28,
          height: 0.2,
          collapsed: false,
        },
        color: "#facc15",
        note: "",
      });
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    updateDraft(
      tool === "highlight"
        ? { kind: "highlight", start: point, end: point }
        : { kind: "drawing", points: [point] },
    );
  }

  function handleAnnotationPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    const current = draftRef.current;
    if (!current || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const point = pointFromPointer(event);
    if (current.kind === "highlight") {
      updateDraft({ ...current, end: point });
      return;
    }
    const last = current.points[current.points.length - 1];
    if (Math.hypot(point[0] - last[0], point[1] - last[1]) < 0.002) return;
    updateDraft({ ...current, points: [...current.points, point].slice(0, 2000) });
  }

  function handleAnnotationPointerUp(event: ReactPointerEvent<SVGSVGElement>) {
    const completedDraft = draftRef.current;
    if (!completedDraft) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (completedDraft.kind === "highlight") {
      const { rects, entryIds, textRanges } = lineHighlightGeometry(
        completedDraft.start,
        completedDraft.end,
        entries,
      );
      if (rects.length > 0) {
        const x0 = Math.min(...rects.map((rect) => rect[0]));
        const y0 = Math.min(...rects.map((rect) => rect[1]));
        const x1 = Math.max(...rects.map((rect) => rect[2]));
        const y1 = Math.max(...rects.map((rect) => rect[3]));
        onCreateAnnotation({
          kind: "highlight",
          page_index: pageNumber - 1,
          geometry: {
            version: 2,
            x0,
            y0,
            x1,
            y1,
            rects,
            entry_ids: entryIds,
            text_ranges: textRanges,
            opacity: annotationOpacity,
          },
          color: annotationColor,
        });
      }
    } else if (completedDraft.points.length > 1) {
      const points = simplifyDrawingPoints(completedDraft.points);
      onCreateAnnotation({
        kind: "drawing",
        page_index: pageNumber - 1,
        geometry: {
          version: 2,
          points,
          stroke_width: drawingStrokeWidth,
          opacity: annotationOpacity,
        },
        color: annotationColor,
      });
    }
    updateDraft(null);
  }

  return (
    <div
      ref={(element) => registerPage(pageNumber, element)}
      className="pdf-page-wrap"
      data-page={pageNumber}
      aria-label={`PDF page ${pageNumber}`}
    >
      <div
        ref={pageElementRef}
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
          {figures
            .filter((figure) => figure.method !== "sequence_inferred")
            .map((figure, index) => {
              const [x0, y0, x1, y1] = rotateBox(figure.box, rotation);
              const hit =
                focusedFigure?.figure_id === figure.figure_id &&
                focusedFigure.page_index === figure.page_index &&
                focusedFigure.box.every(
                  (value, boxIndex) => value === figure.box[boxIndex],
                );
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
          onPointerCancel={() => updateDraft(null)}
        >
          {annotations.filter((annotation) => annotation.kind !== "note").map((annotation) => {
            const interactive = tool === "select";
            const performAction = () => {
              onSelectAnnotation(annotation.id);
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
              const rects = storedHighlightRects(annotation);
              if (rects.length === 0) return null;
              return (
                <g
                  key={annotation.id}
                  className={`pdf-saved-annotation pdf-highlight-mark${selectedAnnotationId === annotation.id ? " selected" : ""}`}
                  style={{
                    opacity: geometryNumber(annotation, "opacity") ?? 0.36,
                    pointerEvents: interactive ? "all" : undefined,
                  }}
                  onPointerDown={activate}
                  onKeyDown={onAnnotationKeyDown}
                  tabIndex={interactive ? 0 : undefined}
                  role={interactive ? "button" : undefined}
                  aria-label={interactive ? `${tool} highlight` : undefined}
                >
                  {rects.map((box, index) => {
                    const [x0, y0, x1, y1] = rotateBox(box, rotation);
                    return (
                      <rect
                        key={index}
                        x={x0}
                        y={y0}
                        width={x1 - x0}
                        height={y1 - y0}
                        fill={annotation.color ?? "#ffe066"}
                      />
                    );
                  })}
                </g>
              );
            }
            if (annotation.kind === "drawing") {
              const points = drawingPoints(annotation).map((point) => rotatePoint(point, rotation));
              if (points.length < 2) return null;
              return (
                <polyline
                  key={annotation.id}
                  className={`pdf-saved-annotation pdf-drawing-mark${selectedAnnotationId === annotation.id ? " selected" : ""}`}
                  points={points.map((point) => point.join(",")).join(" ")}
                  fill="none"
                  stroke={annotation.color ?? "#2456d3"}
                  strokeWidth={geometryNumber(annotation, "stroke_width") ?? 0.004}
                  style={{
                    opacity: geometryNumber(annotation, "opacity") ?? 0.92,
                    pointerEvents: interactive ? "all" : undefined,
                  }}
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
                aria-label={`Select ${annotation.kind}`}
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
            const { rects } = lineHighlightGeometry(draft.start, draft.end, entries);
            return (
              <g className="pdf-highlight-mark draft">
                {rects.map((box, index) => {
                  const [x0, y0, x1, y1] = rotateBox(box, rotation);
                  return (
                    <rect
                      key={index}
                      x={x0}
                      y={y0}
                      width={x1 - x0}
                      height={y1 - y0}
                      fill={annotationColor}
              opacity={annotationOpacity}
                    />
                  );
                })}
              </g>
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
              strokeWidth={drawingStrokeWidth}
              style={{ opacity: annotationOpacity }}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}
        </svg>
        <div className="pdf-sticky-layer" aria-label="PDF notes">
          {annotations
            .filter((annotation) => annotation.kind === "note")
            .map((annotation) => (
              <StickyPdfNote
                key={annotation.id}
                annotation={annotation}
                rotation={rotation}
                pageElement={pageElementRef.current}
                active={activeNoteId === annotation.id}
                tool={tool}
                onActivate={() => onActivateNote(annotation.id)}
                onUpdate={onUpdateAnnotation}
              />
            ))}
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
  onTextHighlightsChange,
  copyCitation,
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
  onTextHighlightsChange?: (highlights: Map<string, PdfTextHighlightRange[]>) => void;
  copyCitation?: string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());
  const updateQueuesRef = useRef(new Map<string, Promise<void>>());
  const visiblePageRef = useRef(1);
  const scrollFrameRef = useRef<number | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1.15);
  const [rotation, setRotation] = useState(0);
  const [tool, setTool] = useState<PdfTool>("select");
  const [highlightColor, setHighlightColor] = useState("#ffe066");
  const [drawingColor, setDrawingColor] = useState("#2456d3");
  const [annotationOpacity, setAnnotationOpacity] = useState(0.6);
  const [drawingStrokeWidth, setDrawingStrokeWidth] = useState(0.004);
  const [annotations, setAnnotations] = useState<PdfAnnotationRead[]>([]);
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<string | null>(null);
  const [activeNoteId, setActiveNoteId] = useState<string | null>(null);
  const [undoStack, setUndoStack] = useState<AnnotationHistory[]>([]);
  const [redoStack, setRedoStack] = useState<AnnotationHistory[]>([]);
  const [historyBusy, setHistoryBusy] = useState(false);

  function recordHistory(record: AnnotationHistory) {
    setUndoStack((items) => [...items.slice(-49), record]);
    setRedoStack([]);
  }

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
        if (!cancelled) {
          setAnnotations(items);
          onTextHighlightsChange?.(textHighlightRanges(items));
        }
      },
      (reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, onTextHighlightsChange]);

  useEffect(() => {
    onTextHighlightsChange?.(textHighlightRanges(annotations));
  }, [annotations, onTextHighlightsChange]);

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
    event.clipboardData.setData(
      "text/plain",
      formatSelectionWithCitation(text, copyCitation ?? ""),
    );
    onSelectionText?.(text);
  }

  async function createAnnotation(annotation: PdfAnnotationCreate, remember = true) {
    try {
      const created = await api.createPdfAnnotation(documentId, annotation);
      setAnnotations((items) => {
        const next = [...items, created];
        onTextHighlightsChange?.(textHighlightRanges(next));
        return next;
      });
      if (remember) {
        recordHistory({ kind: "create", snapshot: created, currentId: created.id });
      }
      setSelectedAnnotationId(created.id);
      setTool("select");
      if (annotation.kind === "note") {
        setActiveNoteId(created.id);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function deleteAnnotation(annotation: PdfAnnotationRead, remember = true) {
    try {
      await updateQueuesRef.current.get(annotation.id)?.catch(() => undefined);
      await api.deletePdfAnnotation(annotation.id);
      if (activeNoteId === annotation.id) setActiveNoteId(null);
      if (selectedAnnotationId === annotation.id) setSelectedAnnotationId(null);
      setAnnotations((items) => {
        const next = removeAnnotationById(items, annotation.id);
        onTextHighlightsChange?.(textHighlightRanges(next));
        return next;
      });
      if (remember) {
        recordHistory({ kind: "delete", snapshot: annotation, currentId: null });
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function updateAnnotation(
    annotation: PdfAnnotationRead,
    update: PdfAnnotationUpdate,
    remember = false,
  ) {
    const after: PdfAnnotationRead = {
      ...annotation,
      note: update.note ?? annotation.note,
      color: update.color ?? annotation.color,
      geometry: update.geometry ?? annotation.geometry,
    };
    if (remember) recordHistory({ kind: "update", before: annotation, after });
    setAnnotations((items) =>
      items.map((item) =>
        item.id === annotation.id
          ? {
              ...item,
              note: update.note ?? item.note,
              color: update.color ?? item.color,
              geometry: update.geometry ?? item.geometry,
            }
          : item,
      ),
    );
    const previous = updateQueuesRef.current.get(annotation.id) ?? Promise.resolve();
    const request = previous
      .catch(() => undefined)
      .then(async () => {
        try {
          await api.updatePdfAnnotation(annotation.id, update);
        } catch (reason) {
          setAnnotations((items) =>
            items.map((item) => {
              if (item.id !== annotation.id) return item;
              return {
                ...item,
                note:
                  update.note !== undefined && item.note === update.note
                    ? annotation.note
                    : item.note,
                color:
                  update.color !== undefined && item.color === update.color
                    ? annotation.color
                    : item.color,
                geometry:
                  update.geometry !== undefined && item.geometry === update.geometry
                    ? annotation.geometry
                    : item.geometry,
              };
            }),
          );
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      });
    updateQueuesRef.current.set(annotation.id, request);
    await request;
    if (updateQueuesRef.current.get(annotation.id) === request) {
      updateQueuesRef.current.delete(annotation.id);
    }
  }

  async function applyHistory(record: AnnotationHistory, direction: "undo" | "redo") {
    if (historyBusy) return;
    setHistoryBusy(true);
    try {
      if (record.kind === "create") {
        if (direction === "undo" && record.currentId) {
          const annotationId = record.currentId;
          await updateQueuesRef.current.get(annotationId)?.catch(() => undefined);
          await api.deletePdfAnnotation(annotationId);
          setAnnotations((items) => removeAnnotationById(items, annotationId));
          record.currentId = null;
        } else if (direction === "redo" && !record.currentId) {
          const restored = await api.createPdfAnnotation(
            documentId,
            annotationCreatePayload(record.snapshot),
          );
          record.currentId = restored.id;
          record.snapshot = restored;
          setAnnotations((items) => [...items, restored]);
        }
      } else if (record.kind === "delete") {
        if (direction === "undo" && !record.currentId) {
          const restored = await api.createPdfAnnotation(
            documentId,
            annotationCreatePayload(record.snapshot),
          );
          record.currentId = restored.id;
          record.snapshot = restored;
          setAnnotations((items) => [...items, restored]);
        } else if (direction === "redo" && record.currentId) {
          const annotationId = record.currentId;
          await updateQueuesRef.current.get(annotationId)?.catch(() => undefined);
          await api.deletePdfAnnotation(annotationId);
          setAnnotations((items) => removeAnnotationById(items, annotationId));
          record.currentId = null;
        }
      } else {
        const target = direction === "undo" ? record.before : record.after;
        await updateQueuesRef.current.get(target.id)?.catch(() => undefined);
        const updated = await api.updatePdfAnnotation(target.id, {
          geometry: target.geometry,
          color: target.color ?? undefined,
          note: target.note ?? undefined,
        });
        setAnnotations((items) =>
          items.map((item) => (item.id === updated.id ? updated : item)),
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally {
      setHistoryBusy(false);
    }
  }

  async function undoAnnotation() {
    const record = undoStack[undoStack.length - 1];
    if (!record) return;
    try {
      await applyHistory(record, "undo");
      setUndoStack((items) => items.slice(0, -1));
      setRedoStack((items) => [...items, record]);
      setSelectedAnnotationId(null);
    } catch {
      // applyHistory surfaces the error without moving the history cursor.
    }
  }

  async function redoAnnotation() {
    const record = redoStack[redoStack.length - 1];
    if (!record) return;
    try {
      await applyHistory(record, "redo");
      setRedoStack((items) => items.slice(0, -1));
      setUndoStack((items) => [...items, record]);
      setSelectedAnnotationId(null);
    } catch {
      // applyHistory surfaces the error without moving the history cursor.
    }
  }

  const pageCount = pdf?.numPages ?? 0;
  const entriesByPage = useMemo(() => groupByPage(entries), [entries]);
  const calloutsByPage = useMemo(() => groupByPage(callouts), [callouts]);
  const figuresByPage = useMemo(() => groupByPage(figures), [figures]);
  const annotationsByPage = useMemo(() => groupByPage(annotations), [annotations]);
  const activeMarkColor = tool === "drawing" ? drawingColor : highlightColor;
  const selectedAnnotation =
    annotations.find((annotation) => annotation.id === selectedAnnotationId) ?? null;
  const selectedMark =
    selectedAnnotation?.kind === "highlight" || selectedAnnotation?.kind === "drawing"
      ? selectedAnnotation
      : null;
  const controlColor = selectedMark?.color ?? activeMarkColor;
  const controlOpacity = selectedMark
    ? (geometryNumber(selectedMark, "opacity") ?? (selectedMark.kind === "highlight" ? 0.36 : 0.92))
    : annotationOpacity;
  const controlStrokeWidth =
    selectedMark?.kind === "drawing"
      ? (geometryNumber(selectedMark, "stroke_width") ?? 0.004)
      : drawingStrokeWidth;

  function handlePaneKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    if (target.matches("input, textarea, select")) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      if (event.shiftKey) void redoAnnotation();
      else void undoAnnotation();
      return;
    }
    if (event.key === "Escape") {
      setTool("select");
      setSelectedAnnotationId(null);
      return;
    }
    if ((event.key === "Delete" || event.key === "Backspace") && selectedAnnotation) {
      event.preventDefault();
      void deleteAnnotation(selectedAnnotation);
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const shortcut: Record<string, PdfTool> = {
      v: "select",
      h: "highlight",
      p: "drawing",
      n: "note",
    };
    const next = shortcut[event.key.toLowerCase()];
    if (next) {
      event.preventDefault();
      setTool(next);
    }
  }

  return (
    <div className="pdf-pane" onPointerDown={onActivate} onKeyDown={handlePaneKeyDown}>
      <div className="pdf-annotation-bar">
        <div className="pdf-view-controls">
          <span className="pdf-pageno">
            Page {page} of {pageCount || "…"}
          </span>
          <button
            type="button"
            className="icon-button"
            onClick={() => setRotation((value) => (value + 90) % 360)}
            title="Rotate 90°"
            aria-label="Rotate PDF 90 degrees"
          >
            <Icon name="rotate-cw" size={17} />
          </button>
        </div>
        <div className="pdf-zoom-controls" role="group" aria-label="PDF magnification">
          <button
            type="button"
            className="icon-button"
            onClick={() => setScale((value) => adjustedPdfScale(value, -1))}
            disabled={scale <= PDF_SCALE_MIN}
            aria-label="Decrease PDF magnification"
            title="Decrease magnification"
          >
            <Icon name="zoom-out" size={17} />
          </button>
          <output className="pdf-zoom-level" aria-live="polite" aria-label="PDF magnification">
            {Math.round(scale * 100)}%
          </output>
          <button
            type="button"
            className="icon-button"
            onClick={() => setScale((value) => adjustedPdfScale(value, 1))}
            disabled={scale >= PDF_SCALE_MAX}
            aria-label="Increase PDF magnification"
            title="Increase magnification"
          >
            <Icon name="zoom-in" size={17} />
          </button>
        </div>
        <span className="pdf-annotation-title">Annotations</span>
        <div className="pdf-annotation-tools" role="toolbar" aria-label="PDF annotation tools">
          {PDF_TOOLS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`secondary${tool === item.id ? " active" : ""}`}
              aria-pressed={tool === item.id}
              aria-label={item.label}
              title={item.label}
              onClick={() => setTool(item.id)}
            >
              <Icon name={item.icon} size={16} />
              <span className="pdf-tool-label">{item.label}</span>
            </button>
          ))}
          <button
            type="button"
            className="secondary"
            disabled={undoStack.length === 0 || historyBusy}
            onClick={() => void undoAnnotation()}
            aria-label="Undo annotation change"
            title="Undo annotation change (Ctrl+Z)"
          >
            <Icon name="undo" size={16} />
          </button>
          <button
            type="button"
            className="secondary"
            disabled={redoStack.length === 0 || historyBusy}
            onClick={() => void redoAnnotation()}
            aria-label="Redo annotation change"
            title="Redo annotation change (Ctrl+Shift+Z)"
          >
            <Icon name="redo" size={16} />
          </button>
          {(tool === "highlight" || tool === "drawing" || selectedMark) && (
            <label className="pdf-color-control">
              <span className="pdf-color-swatch" style={{ backgroundColor: controlColor }} aria-hidden="true" />
              <select
                value={controlColor}
                aria-label="Annotation color"
                onChange={(event) => {
                  if (selectedMark) {
                    void updateAnnotation(selectedMark, { color: event.target.value }, true);
                  } else if (tool === "drawing") setDrawingColor(event.target.value);
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
          {(tool === "highlight" || tool === "drawing" || selectedMark) && (
            <label className="pdf-style-control" title="Annotation opacity">
              <Icon name="sun" size={14} />
              <input
                type="range"
                min="0.1"
                max="1"
                step="0.1"
                value={controlOpacity}
                aria-label="Annotation opacity"
                onChange={(event) => {
                  const opacity = Number(event.target.value);
                  if (selectedMark) {
                    void updateAnnotation(
                      selectedMark,
                      { geometry: { ...selectedMark.geometry, opacity } },
                      true,
                    );
                  } else {
                    setAnnotationOpacity(opacity);
                  }
                }}
              />
            </label>
          )}
          {(tool === "drawing" || selectedMark?.kind === "drawing") && (
            <label className="pdf-style-control" title="Pen width">
              <Icon name="pencil" size={14} />
              <input
                type="range"
                min="0.002"
                max="0.015"
                step="0.001"
                value={controlStrokeWidth}
                aria-label="Drawing stroke width"
                onChange={(event) => {
                  const strokeWidth = Number(event.target.value);
                  if (selectedMark?.kind === "drawing") {
                    void updateAnnotation(
                      selectedMark,
                      { geometry: { ...selectedMark.geometry, stroke_width: strokeWidth } },
                      true,
                    );
                  } else {
                    setDrawingStrokeWidth(strokeWidth);
                  }
                }}
              />
            </label>
          )}
          <button
            type="button"
            className="secondary danger-icon"
            disabled={!selectedAnnotation}
            onClick={() => selectedAnnotation && void deleteAnnotation(selectedAnnotation)}
            aria-label="Delete selected annotation"
            title={selectedAnnotation ? "Delete selected annotation" : "Select an annotation to delete"}
          >
            <Icon name="trash" size={16} />
            <span className="pdf-tool-label">Delete</span>
          </button>
        </div>
      </div>

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
                  entries={(entriesByPage.get(index) ?? []).filter(
                    (entry) => entry.box?.length === 4,
                  )}
                  callouts={(calloutsByPage.get(index) ?? []).filter(
                    (callout) => callout.box?.length === 4,
                  )}
                  figures={(figuresByPage.get(index) ?? []).filter(
                    (figure) => figure.box?.length === 4,
                  )}
                  focusedFigure={focusedFigure}
                  annotations={annotationsByPage.get(index) ?? []}
                  tool={tool}
                  annotationColor={activeMarkColor}
                  annotationOpacity={annotationOpacity}
                  drawingStrokeWidth={drawingStrokeWidth}
                  scale={scale}
                  rotation={rotation}
                  highlightOrdinal={highlightOrdinal}
                  highlightCallouts={highlightCallouts}
                  onSelectLine={onSelectLine}
                  onSelectCallout={onSelectCallout}
                  onCreateAnnotation={(annotation) => void createAnnotation(annotation)}
                  onUpdateAnnotation={(annotation, update) => void updateAnnotation(annotation, update)}
                  selectedAnnotationId={selectedAnnotationId}
                  onSelectAnnotation={setSelectedAnnotationId}
                  activeNoteId={activeNoteId}
                  onActivateNote={(annotationId) => {
                    setActiveNoteId(annotationId);
                    setSelectedAnnotationId(annotationId);
                  }}
                  registerPage={registerPage}
                />
              );
            })}
        </div>
      </div>
    </div>
  );
}
