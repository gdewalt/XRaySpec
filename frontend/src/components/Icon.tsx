import type { SVGProps } from "react";

export type IconName =
  | "arrow-left" | "arrow-up-right" | "bookmark" | "check" | "chevron-right"
  | "columns" | "file" | "file-text" | "folder" | "import" | "library"
  | "lock" | "log-out" | "plus" | "refresh" | "scan" | "search"
  | "text" | "trash" | "upload";

const PATHS: Record<IconName, string[]> = {
  "arrow-left": ["M19 12H5", "m12 19-7-7 7-7"],
  "arrow-up-right": ["M7 17 17 7", "M7 7h10v10"],
  bookmark: ["M6 4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18l-6-4-6 4Z"],
  check: ["m5 12 4 4L19 6"],
  "chevron-right": ["m9 18 6-6-6-6"],
  columns: ["M3 4h18v16H3z", "M12 4v16"],
  file: ["M6 2h8l4 4v16H6z", "M14 2v5h5"],
  "file-text": ["M6 2h8l4 4v16H6z", "M14 2v5h5", "M9 13h6", "M9 17h6"],
  folder: ["M3 6h7l2 2h9v11H3z"],
  import: ["M12 3v12", "m7 10 5 5 5-5", "M4 21h16"],
  library: ["M4 19V5", "M9 19V5", "M14 19V5", "M19 19V5", "M2 5h20", "M2 19h20"],
  lock: ["M6 10h12v10H6z", "M8 10V7a4 4 0 0 1 8 0v3"],
  "log-out": ["M10 17l5-5-5-5", "M15 12H3", "M15 4h5v16h-5"],
  plus: ["M12 5v14", "M5 12h14"],
  refresh: ["M20 7v5h-5", "M4 17v-5h5", "M18.5 9A7 7 0 0 0 6 6.5L4 9", "M5.5 15A7 7 0 0 0 18 17.5l2-2.5"],
  scan: ["M4 8V4h4", "M16 4h4v4", "M20 16v4h-4", "M8 20H4v-4", "M8 12h8"],
  search: ["M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16Z", "m21 21-4.35-4.35"],
  text: ["M4 5h16", "M8 5v14", "M16 5v14", "M6 19h4", "M14 19h4"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M7 7l1 14h8l1-14", "M10 11v6", "M14 11v6"],
  upload: ["M12 21V9", "m7 14 5-5 5 5", "M5 3h14"],
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
