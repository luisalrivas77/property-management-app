// Line icons for the menu and the panel (phase 10, mockups 14-C2 and 15-B2): 24-unit paths drawn
// with currentColor, decorative only (aria-hidden), so a label always carries the meaning.
import type { ReactNode } from "react";

const PATHS = {
  overview: (
    <>
      <path d="M4 10.5 12 4l8 6.5" />
      <path d="M6 9v10h12V9" />
      <path d="M10 19v-5h4v5" />
    </>
  ),
  inbox: (
    <>
      <path d="M4 13h4l1.5 2.5h5L16 13h4" />
      <path d="M5.5 6h13L20 13v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-5z" />
    </>
  ),
  household: (
    <>
      <rect x="4" y="6" width="16" height="13" rx="2" />
      <path d="M16 12.5h4" />
      <path d="M7 6V4.5h7V6" />
    </>
  ),
  assets: (
    <>
      <path d="M4 4v16h16" />
      <path d="m7.5 14 3.5-4 3 3 5-6" />
    </>
  ),
  more: (
    <>
      <circle cx="6" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="18" cy="12" r="1.4" fill="currentColor" stroke="none" />
    </>
  ),
  accounts: (
    <>
      <path d="M3.5 9 12 4.5 20.5 9" />
      <path d="M5 9.5v8M9.5 9.5v8M14.5 9.5v8M19 9.5v8" />
      <path d="M3.5 19.5h17" />
    </>
  ),
  properties: (
    <>
      <path d="M4 11 12 5l8 6" />
      <path d="M6 10v9h12v-9" />
      <rect x="10" y="13" width="4" height="6" />
    </>
  ),
  tax: (
    <>
      <path d="M6 3.5h8l4 4V20a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 6 20z" />
      <path d="m9.5 16.5 5-5" />
      <circle cx="10" cy="12" r=".9" fill="currentColor" stroke="none" />
      <circle cx="14" cy="16" r=".9" fill="currentColor" stroke="none" />
    </>
  ),
  planning: (
    <>
      <path d="m3.5 16.5 5.5-5.5 4 4 7-7" />
      <path d="M15 8h5v5" />
    </>
  ),
  documents: (
    <>
      <path d="M6 3.5h8l4 4V20a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 6 20z" />
      <path d="M14 3.5V8h4" />
      <path d="M9 12.5h6M9 16h6" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" />
    </>
  ),
  upload: (
    <>
      <path d="M12 15.5V4.5" />
      <path d="m7.5 9 4.5-4.5L16.5 9" />
      <path d="M5 15v4h14v-4" />
    </>
  ),
  camera: (
    <>
      <path d="M4 8.5h3.5L9 6h6l1.5 2.5H20V18H4z" />
      <circle cx="12" cy="13" r="3" />
    </>
  ),
  chevronRight: <path d="m9 6 6 6-6 6" />,
  chevronLeft: <path d="m15 6-6 6 6 6" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 22, className }: { name: IconName; size?: number; className?: string }) {
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
    >
      {PATHS[name]}
    </svg>
  );
}
