import { useEffect, useState } from "react"
import { getDecisions } from "../api"
import { Card, EmptyNote, KpiCard, Td, Th } from "./parts"
import { fmtDecimal, fmtPct, fmtSeconds } from "./format"
import { EVAL_REPORT, type EvalModelPoint } from "../evalReport"

/**
 * The evaluation screen, fed by the measured quality matrix (NAU-14) whose
 * report ships as `eval/data/report.json` and is snapshotted into
 * `evalReport.ts`. The gateway serves no endpoint with quality measurements,
 * so there is no live fetch for the matrix itself — only the mean Clef
 * decision latency, which comes from the same `/v1/decisions` read API the
 * feed uses. Nothing is invented: what the report does not measure, the
 * screen says so (no confusion matrix yet).
 */

const POLICY_LABELS: Record<string, string> = {
  R0: "siempre el modelo por defecto",
  R1: "siempre el modelo más capaz",
  R2: "siempre el modelo más rápido",
  R3: "el router (Clef decide por petición)",
}

export function EvaluationScreen() {
  const [meanClefLatencyMs, setMeanClefLatencyMs] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    getDecisions(200)
      .then((result) => {
        if (cancelled) return
        const values = result.decisions
          .filter((decision) => decision.decision_source === "clef")
          .map((decision) => decision.clef_latency_ms)
        setMeanClefLatencyMs(
          values.length === 0
            ? null
            : values.reduce((sum, value) => sum + value, 0) / values.length,
        )
      })
      .catch(() => {
        if (!cancelled) setMeanClefLatencyMs(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const report = EVAL_REPORT
  const r3 = report.pareto.policies.find((policy) => policy.policy === "R3")
  const r1 = report.baselines.R1
  const bestUniqueQuality = Math.max(...report.pareto.models.map((m) => m.quality))
  const retention = r3 == null || bestUniqueQuality === 0 ? null : r3.quality / bestUniqueQuality
  const latencyReduction = r3 == null ? null : (r1.latency_ms - r3.latency_ms) / r1.latency_ms
  const coverage = report.baselines.R3?.coverage ?? null

  return (
    <section className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Retención de calidad"
          value={retention == null ? "sin dato" : fmtPct(retention)}
          sub="R3 frente al mejor modelo único"
        />
        <KpiCard
          label="Reducción de latencia"
          value={
            latencyReduction == null
              ? "sin dato"
              : `${latencyReduction < 0 ? "−" : ""}${fmtDecimal(Math.abs(latencyReduction) * 100, 1)}%`
          }
          sub="R3 frente a R1 (siempre el más capaz)"
        />
        <KpiCard
          label="Cobertura"
          value={coverage == null ? "sin dato" : fmtPct(coverage)}
          sub={`${report.prompts} prompts etiquetados`}
        />
        <KpiCard
          label="Costo de decisión"
          value={meanClefLatencyMs == null ? "sin dato" : fmtSeconds(meanClefLatencyMs)}
          sub="latencia media de Clef (decisiones recientes)"
        />
      </div>

      {!report.complete && (
        <p className="rounded-[14px] bg-chip-amber-bg px-4 py-3 text-xs text-chip-amber-text">
          El informe de evaluación está incompleto: faltan {report.missing_calls} llamada(s). Los
          números de esta pantalla describen solo la muestra medida.
        </p>
      )}

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <Card title="Calidad frente a latencia">
          <QualityLatencyScatter models={report.pareto.models} router={r3} />
          <p className="mt-3 text-xs text-muted">
            El router (R3) no tiene por qué quedar por encima de la nube de puntos: optimiza la
            elección por petición, y estos números describen solo los {report.prompts} prompts
            etiquetados de la muestra — «{report.note}».
          </p>
        </Card>

        <Card title="Baselines">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <Th>Id</Th>
                  <Th>Política</Th>
                  <Th>Calidad</Th>
                  <Th>Latencia</Th>
                  <Th>Cobertura</Th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(report.baselines).map(([id, baseline]) => (
                  <tr key={id} className="border-b border-line/60 last:border-b-0">
                    <Td mono className="font-medium text-ink">
                      {id}
                    </Td>
                    <Td>{POLICY_LABELS[id] ?? baseline.model ?? id}</Td>
                    <Td mono>{fmtDecimal(baseline.quality)}</Td>
                    <Td mono>{fmtSeconds(baseline.latency_ms)}</Td>
                    <Td mono>{fmtPct(baseline.coverage)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card>
        <p className="text-xs text-muted">
          Sin matriz de confusión: hace falta un conjunto de predicciones de Clef etiquetadas (p.
          ej. la pregunta <code className="font-mono">task</code> frente a la verdad conocida) que
          todavía no se ha recogido. Cuando exista detrás de una API, esta pantalla la leerá.
        </p>
      </Card>
    </section>
  )
}

// --- scatter (inline SVG, no chart library) ---------------------------------

const WIDTH = 560
const HEIGHT = 340
const PAD = { top: 16, right: 120, bottom: 40, left: 52 }

interface PlotPoint {
  model: string
  latencySec: number
  quality: number
  router: boolean
}

function QualityLatencyScatter({
  models,
  router,
}: {
  models: EvalModelPoint[]
  router: { quality: number; latency_ms: number } | undefined
}) {
  const points: PlotPoint[] = models
    .map((model) => ({
      model: model.model,
      latencySec: model.latency_ms / 1000,
      quality: model.quality,
      router: false,
    }))
  if (router != null) {
    points.push({
      model: "Router (R3)",
      latencySec: router.latency_ms / 1000,
      quality: router.quality,
      router: true,
    })
  }
  if (points.length === 0) {
    return <EmptyNote>El informe no trae puntos que dibujar.</EmptyNote>
  }

  const latencies = points.map((point) => point.latencySec)
  const qualities = points.map((point) => point.quality)
  // Axis ranges with a little headroom, always starting at a sane floor.
  const xMin = 0
  const xMax = niceCeil(Math.max(...latencies))
  const yMin = Math.floor(Math.min(...qualities) * 2 - 0.3) / 2 // half-point steps
  const yMax = niceCeil(Math.max(...qualities))
  const plotW = WIDTH - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const x = (latency: number) => PAD.left + ((latency - xMin) / (xMax - xMin)) * plotW
  const y = (quality: number) => PAD.top + (1 - (quality - yMin) / (yMax - yMin)) * plotH

  const yTicks = 4
  const xTicks = 5

  return (
    <figure>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="Dispersión de calidad frente a latencia por modelo"
        className="w-full"
      >
        {/* grid + y axis ticks (quality, 0–5 scale of the eval) */}
        {Array.from({ length: yTicks + 1 }, (_, index) => {
          const value = yMin + ((yMax - yMin) * index) / yTicks
          return (
            <g key={`y${index}`}>
              <line
                x1={PAD.left}
                x2={WIDTH - PAD.right}
                y1={y(value)}
                y2={y(value)}
                stroke="var(--color-line)"
                strokeWidth="1"
              />
              <text
                x={PAD.left - 8}
                y={y(value) + 3}
                textAnchor="end"
                fontSize="10"
                fill="var(--color-muted)"
                fontFamily="var(--font-mono)"
              >
                {value.toFixed(1).replace(".", ",")}
              </text>
            </g>
          )
        })}
        {/* x axis ticks (latency in seconds) */}
        {Array.from({ length: xTicks + 1 }, (_, index) => {
          const value = xMin + ((xMax - xMin) * index) / xTicks
          return (
            <g key={`x${index}`}>
              <line
                x1={x(value)}
                x2={x(value)}
                y1={PAD.top}
                y2={HEIGHT - PAD.bottom}
                stroke="var(--color-line)"
                strokeWidth="1"
              />
              <text
                x={x(value)}
                y={HEIGHT - PAD.bottom + 16}
                textAnchor="middle"
                fontSize="10"
                fill="var(--color-muted)"
                fontFamily="var(--font-mono)"
              >
                {fmtDecimal(value, 0)} s
              </text>
            </g>
          )
        })}
        {/* hollow gray dots: one per unique model */}
        {points
          .filter((point) => !point.router)
          .map((point) => (
            <g key={point.model}>
              <circle
                cx={x(point.latencySec)}
                cy={y(point.quality)}
                r="4.5"
                fill="var(--color-surface)"
                stroke="var(--color-faint)"
                strokeWidth="1.5"
              />
              <text
                x={x(point.latencySec) + 8}
                y={y(point.quality) + 3}
                fontSize="9.5"
                fill="var(--color-muted)"
                fontFamily="var(--font-mono)"
              >
                {point.model}
              </text>
            </g>
          ))}
        {/* solid teal dot: the router */}
        {points
          .filter((point) => point.router)
          .map((point) => (
            <g key={point.model}>
              <circle
                cx={x(point.latencySec)}
                cy={y(point.quality)}
                r="5.5"
                fill="var(--color-accent)"
              />
              <text
                x={x(point.latencySec) + 9}
                y={y(point.quality) + 3}
                fontSize="10"
                fontWeight="600"
                fill="var(--color-accent)"
                fontFamily="var(--font-mono)"
              >
                {point.model}
              </text>
            </g>
          ))}
        {/* axis captions */}
        <text
          x={(WIDTH - PAD.right + PAD.left) / 2}
          y={HEIGHT - 4}
          textAnchor="middle"
          fontSize="10"
          fill="var(--color-muted)"
        >
          latencia (s)
        </text>
        <text
          x={12}
          y={PAD.top + plotH / 2}
          textAnchor="middle"
          fontSize="10"
          fill="var(--color-muted)"
          transform={`rotate(-90 12 ${PAD.top + plotH / 2})`}
        >
          calidad media
        </text>
      </svg>
    </figure>
  )
}

/** Round an axis max up to a tidy value (never below the data). */
function niceCeil(value: number): number {
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(value, 1)))
  return Math.ceil(value / magnitude) * magnitude
}
