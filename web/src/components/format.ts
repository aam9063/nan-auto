// Pure formatting only. Everything the dashboard shows comes from the server;
// this module never computes data, it only renders what arrived.
export function fmtMs(value: number | null | undefined): string {
  if (value == null) return "—"
  const ms = Math.round(value)
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`
}

export function fmtTokens(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString()
}

export function fmtTime(iso: string | null | undefined): string {
  if (iso == null) return "—"
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString()
}

export function fmtPct(value: number): string {
  return `${Math.round(value * 1000) / 10}%`
}
