import { fmtPct, fmtPctEs } from "./format"
import type { ChoiceProbabilities, ScoreProbabilities } from "../types"

interface BarRow {
  key: string
  value: number
}

/**
 * One clef question's distribution, exactly as the playground payload
 * carries it: probability bars per label (or scale level), plus confidence.
 * The score questions' legend is not part of the payload, so bars are keyed
 * by the level the gateway sent — nothing is renamed or invented here.
 */
export function ProbabilityBars({
  questionId,
  answer,
}: {
  questionId: string
  answer: ChoiceProbabilities | ScoreProbabilities | { yes: number; confidence: number }
}) {
  const titles: Record<string, string> = {
    task: "Tipo de tarea",
    difficulty: "Dificultad",
    needs_reasoning: "Necesita razonamiento",
    expected_output_length: "Longitud esperada de salida",
  }
  let rows: BarRow[]
  let subtitle: string | null
  let confidence: number
  if ("probabilities" in answer && answer.probabilities != null) {
    rows = Object.entries(answer.probabilities)
      .map(([key, value]) => ({ key, value: Number(value) }))
      .sort((a, b) => b.value - a.value)
    confidence = answer.confidence
    subtitle =
      "label" in answer && answer.label != null
        ? answer.label
        : "score" in answer && answer.score != null
          ? `score ${answer.score}`
          : null
  } else if ("yes" in answer && answer.yes != null) {
    rows = [{ key: "yes", value: Number(answer.yes) }]
    confidence = answer.confidence
    subtitle = null
  } else {
    return null
  }
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="flex items-baseline justify-between gap-4">
        <span className="font-mono text-[13px] font-medium text-ink">
          {titles[questionId] ?? questionId}
        </span>
        {subtitle != null && (
          <span className="font-mono text-xs text-muted">{subtitle}</span>
        )}
        <span className="ml-auto font-mono text-xs tabular-nums text-faint">
          confianza {fmtPctEs(confidence)}
        </span>
      </div>
      <div className="mt-2 space-y-1.5">
        {rows.map((row) => (
          <div key={row.key} className="flex items-center gap-2 text-xs">
            <span className="w-32 shrink-0 truncate text-body">{row.key}</span>
            <div className="h-2 flex-1 overflow-hidden rounded-sm bg-canvas">
              <div
                className="h-full rounded-sm bg-accent"
                style={{ width: `${Math.max(0, Math.min(100, row.value * 100))}%` }}
              />
            </div>
            <span className="w-12 shrink-0 text-right font-mono tabular-nums text-muted">
              {fmtPct(row.value)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
