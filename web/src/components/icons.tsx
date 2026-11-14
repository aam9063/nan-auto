/** Inline SVG icons for the sidebar (24px grid, stroked, currentColor). */

function base(path: React.ReactNode): React.ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="size-4 shrink-0"
    >
      {path}
    </svg>
  )
}

export function PlayIcon() {
  return base(<path d="M8 5.5v13l10-6.5z" />)
}

export function ActivityIcon() {
  return base(<path d="M3 12h4l3-8 4 16 3-8h4" />)
}

export function RowsIcon() {
  return base(
    <>
      <rect x="4" y="5" width="16" height="4.5" rx="1.5" />
      <rect x="4" y="14.5" width="16" height="4.5" rx="1.5" />
    </>,
  )
}

export function ChartIcon() {
  return base(
    <>
      <path d="M4 20V4" />
      <path d="M4 20h16" />
      <path d="M8 16v-4" />
      <path d="M13 16V8" />
      <path d="M18 16v-6" />
    </>,
  )
}

/** The wordmark mark: a small node network inside the teal rounded square. */
export function NodesIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="size-5 text-white"
    >
      <circle cx="12" cy="6" r="2.2" />
      <circle cx="6" cy="17" r="2.2" />
      <circle cx="18" cy="17" r="2.2" />
      <path d="M10.4 7.8 7.4 15M13.6 7.8l3 7.2M8.2 17h7.6" />
    </svg>
  )
}
