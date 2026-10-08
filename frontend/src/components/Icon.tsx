import type { SVGProps } from "react";

export type IconName =
  | "arrow-left" | "arrow-up-right" | "bookmark" | "check" | "chevron-down"
  | "chevron-right" | "chevron-up" | "circle-info" | "copy" | "download"
  | "columns" | "file" | "file-text" | "folder" | "import" | "library"
  | "highlighter" | "link" | "lock" | "log-out" | "message-square" | "more-horizontal"
  | "mouse-pointer" | "panel-left" | "pencil" | "plus" | "quote" | "refresh"
  | "rotate-cw" | "scan" | "search" | "settings" | "text" | "trash" | "upload"
  | "zoom-in" | "zoom-out";

const PATHS: Record<IconName, string[]> = {
  "arrow-left": ["M19 12H5", "m12 19-7-7 7-7"],
  "arrow-up-right": ["M7 17 17 7", "M7 7h10v10"],
  bookmark: ["M6 4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18l-6-4-6 4Z"],
  check: ["m5 12 4 4L19 6"],
  "chevron-down": ["m6 9 6 6 6-6"],
  "chevron-right": ["m9 18 6-6-6-6"],
  "chevron-up": ["m18 15-6-6-6 6"],
  "circle-info": ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z", "M12 10v6", "M12 7h.01"],
  copy: ["M8 8h11v13H8z", "M16 8V3H3v13h5"],
  download: ["M12 3v12", "m7 10 5 5 5-5", "M4 21h16"],
  columns: ["M3 4h18v16H3z", "M12 4v16"],
  file: ["M6 2h8l4 4v16H6z", "M14 2v5h5"],
  "file-text": ["M6 2h8l4 4v16H6z", "M14 2v5h5", "M9 13h6", "M9 17h6"],
  folder: ["M3 6h7l2 2h9v11H3z"],
  highlighter: ["m9 11-6 6v4h4l6-6", "m15 13 5-5-4-4-5 5", "M7 21h13"],
  import: ["M12 3v12", "m7 10 5 5 5-5", "M4 21h16"],
  link: ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
  library: ["M4 19V5", "M9 19V5", "M14 19V5", "M19 19V5", "M2 5h20", "M2 19h20"],
  lock: ["M6 10h12v10H6z", "M8 10V7a4 4 0 0 1 8 0v3"],
  "log-out": ["M10 17l5-5-5-5", "M15 12H3", "M15 4h5v16h-5"],
  "message-square": ["M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"],
  "more-horizontal": ["M5 12h.01", "M12 12h.01", "M19 12h.01"],
  "mouse-pointer": ["m3 3 7.1 17 2.15-6.2L18 12Z", "m13.2 13.2 4.8 4.8"],
  "panel-left": ["M3 4h18v16H3z", "M9 4v16", "M5.5 8h1", "M5.5 12h1"],
  pencil: ["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z", "m14.5 5.5 3 3"],
  plus: ["M12 5v14", "M5 12h14"],
  quote: ["M3 21c3 0 7-1 7-8V5H3v8h4c0 3-1 5-4 6", "M14 21c3 0 7-1 7-8V5h-7v8h4c0 3-1 5-4 6"],
  refresh: ["M20 7v5h-5", "M4 17v-5h5", "M18.5 9A7 7 0 0 0 6 6.5L4 9", "M5.5 15A7 7 0 0 0 18 17.5l2-2.5"],
  "rotate-cw": ["M21 12a9 9 0 1 1-2.64-6.36L21 8", "M21 3v5h-5"],
  scan: ["M4 8V4h4", "M16 4h4v4", "M20 16v4h-4", "M8 20H4v-4", "M8 12h8"],
  search: ["M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16Z", "m21 21-4.35-4.35"],
  settings: ["M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z", "M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.12 2.12-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.02 1.55V20h-3v-.09a1.7 1.7 0 0 0-1.02-1.55 1.7 1.7 0 0 0-1.88.34l-.06.06-2.12-2.12.06-.06A1.7 1.7 0 0 0 7 14.7a1.7 1.7 0 0 0-1.55-1.02H5.3v-3h.09A1.7 1.7 0 0 0 7 9.66a1.7 1.7 0 0 0-.34-1.88l-.06-.06L8.72 5.6l.06.06A1.7 1.7 0 0 0 10.66 6a1.7 1.7 0 0 0 1.02-1.55V4.3h3v.09A1.7 1.7 0 0 0 15.7 6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.12 2.12-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.55 1.02H21v3h-.09A1.7 1.7 0 0 0 19.4 15Z"],
  text: ["M4 5h16", "M8 5v14", "M16 5v14", "M6 19h4", "M14 19h4"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M7 7l1 14h8l1-14", "M10 11v6", "M14 11v6"],
  upload: ["M12 21V9", "m7 14 5-5 5 5", "M5 3h14"],
  "zoom-in": ["M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16Z", "m21 21-4.35-4.35", "M11 8v6", "M8 11h6"],
  "zoom-out": ["M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16Z", "m21 21-4.35-4.35", "M8 11h6"],
};

export function Icon({
  name,
  size = 18,
  className,
  ...props
}: {
  name: IconName;
  size?: number;
  className?: string;
} & Omit<SVGProps<SVGSVGElement>, "name">) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {PATHS[name].map((path, index) => <path key={index} d={path} />)}
    </svg>
  );
}
