/** The brand mark: a green rounded square with a house, as in the mockups. Decorative only;
 * the "Hausbuch" text next to it carries the name. */
export function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
        focusable="false"
      >
        <path d="M3 11 12 4l9 7v9H3z" />
        <path d="M9 20v-6h6v6" />
      </svg>
    </span>
  );
}
