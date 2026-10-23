export function BrandIcon({ size = 24 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M3.5 4.5h6.75v15H3.5zM13.75 4.5h6.75v15h-6.75z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path
        d="M5.75 8h2.3M15.95 8h2.3M5.75 16h2.3M15.95 16h2.3"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <circle cx="12" cy="12" r="5.4" fill="currentColor" />
      <path
        d="M12 8.35v7.3M9.05 10.25h1.45M13.5 10.25h1.45M9.05 12h1.45M13.5 12h1.45M9.05 13.75h1.45M13.5 13.75h1.45"
        stroke="#000"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
      <path
        d="m15.85 15.85 4.15 4.15"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
    </svg>
  );
}
