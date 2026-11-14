/** Small shared pieces the screens reuse: cards, chips, KPI tiles, buttons,
 *  table cells and the honest refusal panel. Every color comes from the
 *  theme tokens in index.css — no hex values live in the components. */
import type { ButtonHTMLAttributes, ReactNode } from "react"

// --- surfaces ---------------------------------------------------------------

export function Card({
  title,
  actions,
  children,
  className = "",
}: {
  title?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={`rounded-[14px] border border-line bg-surface p-5 ${className}`}
    >
      {(title != null || actions != null) && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          {title != null && (
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
          )}
          {actions}
        </div>
      )}
      {children}
    </section>
  )
}

export function KpiCard({
  label,
  value,
  sub,
}: {
  label: string
  value: string
  sub?: string
}) {
  return (
    <div className="rounded-[14px] border border-line bg-surface p-5">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-[28px] font-semibold tabular-nums text-ink">
        {value}
      </p>
      {sub != null && <p className="mt-1 text-xs text-faint">{sub}</p>}
    </div>
  )
}

/** One honest placeholder line while (or because) data is missing. */
export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>
}

// --- buttons ----------------------------------------------------------------

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "filled" | "outline"
}

export function UiButton({
  variant = "outline",
  className = "",
  type = "button",
  ...rest
}: ButtonProps) {
  const styles =
    variant === "filled"
      ? "bg-accent text-white hover:bg-accent-strong disabled:cursor-not-allowed disabled:bg-chip-gray-bg disabled:text-faint"
      : "border border-line bg-surface text-ink hover:border-faint disabled:cursor-not-allowed disabled:text-faint"
  return (
    <button
      type={type}
      className={`rounded-lg px-4 py-1.5 text-sm font-semibold transition-colors ${styles} ${className}`}
      {...rest}
    />
  )
}

// --- chips ------------------------------------------------------------------

const CHIP_TONES: Record<string, string> = {
  blue: "bg-chip-blue-bg text-chip-blue-text",
  teal: "bg-chip-teal-bg text-chip-teal-text",
  violet: "bg-chip-violet-bg text-chip-violet-text",
  amber: "bg-chip-amber-bg text-chip-amber-text",
  red: "bg-chip-red-bg text-chip-red-text",
  gray: "bg-chip-gray-bg text-chip-gray-text",
}

export function Chip({
  tone,
  label,
  dot = false,
}: {
  tone: keyof typeof CHIP_TONES
  label: string
  dot?: boolean
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${CHIP_TONES[tone]}`}
    >
      {dot && <span aria-hidden className="size-1.5 rounded-full bg-current" />}
      {label}
    </span>
  )
}

/** Tier chips, colored per mockup: the verbatim tier value is the label. */
export function TierChip({ tier }: { tier: string }) {
  const key = tier.toLowerCase()
  const tone: keyof typeof CHIP_TONES = key.startsWith("t1")
    ? "blue"
    : key.startsWith("t2")
      ? "teal"
      : key.startsWith("t3")
        ? "violet"
        : key === "premium"
          ? "amber"
          : "gray"
  return <Chip tone={tone} label={tier === "" ? "—" : tier} />
}

/** Registry state chip: disponible, degradado (cuota agotada) o caído. */
export function StateChip({
  available,
  degraded,
}: {
  available: boolean
  degraded: boolean
}) {
  if (!available) return <Chip tone="red" label="no disponible" />
  if (degraded) return <Chip tone="amber" label="degradado" />
  return <Chip tone="teal" label="disponible" dot />
}

// --- legacy badge + refusal, restyled (same props as before) ----------------

export function StatusBadge({
  tone,
  label,
}: {
  tone: "good" | "warn" | "bad" | "neutral"
  label: string
}) {
  const tones: Record<string, keyof typeof CHIP_TONES> = {
    good: "teal",
    warn: "amber",
    bad: "red",
    neutral: "gray",
  }
  return <Chip tone={tones[tone]} label={label} />
}

/** Every failure state uses the same shape: what happened, and the server's words. */
export function DecisionRefusal({ title, message }: { title: string; message: string }) {
  return (
    <div className="rounded-[14px] border border-chip-red-text/30 bg-chip-red-bg/40 p-4">
      <h3 className="text-sm font-semibold text-chip-red-text">{title}</h3>
      <p className="mt-1 break-words text-xs text-chip-red-text/90">{message}</p>
    </div>
  )
}

// --- tables -----------------------------------------------------------------

export function Th({ children }: { children: string }) {
  return (
    <th className="px-3 py-2 text-xs font-medium tracking-wide text-muted">
      {children}
    </th>
  )
}

export function Td({
  children,
  mono = false,
  className = "",
}: {
  children: ReactNode
  mono?: boolean
  className?: string
}) {
  return (
    <td
      className={`whitespace-nowrap px-3 py-2 text-body ${mono ? "font-mono text-[13px] tabular-nums" : ""} ${className}`}
    >
      {children}
    </td>
  )
}

/** A light gray strip for reason strings and ids — the mockup's code line. */
export function CodeStrip({ children }: { children: ReactNode }) {
  return (
    <code className="block overflow-x-auto rounded-lg bg-canvas px-3 py-2 font-mono text-[13px] text-body">
      {children}
    </code>
  )
}
