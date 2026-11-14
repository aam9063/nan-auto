/** Small shared pieces the screens reuse: badges and the honest refusal panel. */

export function StatusBadge({
  tone,
  label,
}: {
  tone: "good" | "warn" | "bad" | "neutral"
  label: string
}) {
  const tones: Record<string, string> = {
    good: "border-emerald-700 bg-emerald-950/40 text-emerald-300",
    warn: "border-amber-700 bg-amber-950/40 text-amber-300",
    bad: "border-red-700 bg-red-950/40 text-red-300",
    neutral: "border-neutral-700 bg-neutral-800/60 text-neutral-300",
  }
  return (
    <span
      className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {label}
    </span>
  )
}

/** Every failure state uses the same shape: what happened, and the server's words. */
export function DecisionRefusal({ title, message }: { title: string; message: string }) {
  return (
    <div className="rounded-lg border border-red-800/60 bg-red-950/20 p-4">
      <h3 className="text-sm font-semibold text-red-300">{title}</h3>
      <p className="mt-1 break-words text-xs text-red-200/90">{message}</p>
    </div>
  )
}
