import { useState } from "react"
import { useLiveFeed, type FeedStatus } from "../hooks/useLiveFeed"
import { fmtSeconds, fmtTime, fmtTokens } from "./format"
import { Card, Chip, EmptyNote, KpiCard, Td, Th } from "./parts"
import type { ClefAnswer, DecisionRecord } from "../types"

const STATUS_BADGE: Record<FeedStatus, { tone: "teal" | "amber" | "gray"; label: string }> = {
  connecting: { tone: "gray", label: "conectando…" },
  live: { tone: "teal", label: "En directo" },
  reconnecting: { tone: "amber", label: "flujo caído — reintentando" },
}

/** Percentile of an unsorted sample (nearest-rank), or null when empty. */
function percentile(values: number[], p: number): number | null {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const index = Math.min(sorted.length - 1, Math.ceil(p * sorted.length) - 1)
  return sorted[Math.max(0, index)]
}

export function LiveFeedScreen() {
  const { status, events, message } = useLiveFeed()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // KPIs are derived only from what arrived; nothing is invented here.
  // The window's end is the newest record's timestamp (not Date.now()) so the
  // render stays pure and the rate reflects feed time, not wall-clock skew.
  const newestAt = events.reduce((latest, event) => {
    const created = event.created_at == null ? null : new Date(event.created_at).getTime()
    if (created != null && !Number.isNaN(created) && created > latest) return created
    return latest
  }, 0)
  const perMinute =
    newestAt === 0
      ? 0
      : events.filter((event) => {
          const created = event.created_at == null ? null : new Date(event.created_at).getTime()
          return created != null && !Number.isNaN(created) && newestAt - created <= 60_000
        }).length
  const clefLatencies = events
    .filter((event) => event.decision_source === "clef")
    .map((event) => event.clef_latency_ms)
  const p50 = percentile(clefLatencies, 0.5)
  const p95 = percentile(clefLatencies, 0.95)
  const withFallback = events.filter((event) => event.decision_source === "fallback").length
  const fallbackPct = events.length === 0 ? null : withFallback / events.length
  const selected = selectedId == null ? null : (events.find((e) => e.id === selectedId) ?? null)

  return (
    <section className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Peticiones por minuto"
          value={String(perMinute)}
          sub="en el último minuto del registro"
        />
        <KpiCard
          label="Decisión p50 / p95"
          value={p50 == null ? "—" : fmtSeconds(p50)}
          sub={p95 == null ? "sin decisiones de Clef" : `p95 ${fmtSeconds(p95)}`}
        />
        <KpiCard
          label="Con fallback"
          value={fallbackPct == null ? "—" : `${Math.round(fallbackPct * 100)}%`}
          sub={`${withFallback} de ${events.length} decisiones`}
        />
        <KpiCard
          label="Total en el registro"
          value={String(events.length)}
          sub="decisiones cargadas"
        />
      </div>

      {message != null && (
        <p className="rounded-[14px] bg-chip-amber-bg px-4 py-3 text-xs text-chip-amber-text">
          {message}
        </p>
      )}

      <Card
        title="Peticiones"
        actions={
          <Chip
            tone={STATUS_BADGE[status].tone}
            dot={status === "live"}
            label={STATUS_BADGE[status].label}
          />
        }
      >
        {events.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line p-6">
            <EmptyNote>
              Todavía no se ha enrutado nada. Envía una petición a través del gateway — por ejemplo
              con el botón «Enviar y ver la respuesta» del Playground — y aparecerá aquí en cuanto
              el gateway la registre.
            </EmptyNote>
          </div>
        ) : (
          <div className="-mx-2 overflow-x-auto">
            <table className="w-full min-w-200 text-left text-sm">
              <thead>
                <tr className="border-b border-line text-muted">
                  <Th>Hora</Th>
                  <Th>Modelo</Th>
                  <Th>Perfil</Th>
                  <Th>Razonamiento</Th>
                  <Th>Origen</Th>
                  <Th>Tokens (e/s)</Th>
                  <Th>Fallbacks</Th>
                  <Th>Decisión</Th>
                </tr>
              </thead>
              <tbody>
                {events.map((record) => {
                  const fallbacks = record.attempts.filter(
                    (attempt) => attempt.outcome !== "success",
                  )
                  const hasTimings =
                    record.ttft_ms != null ||
                    record.total_ms != null ||
                    record.prompt_tokens != null ||
                    record.completion_tokens != null
                  return (
                    <tr
                      key={record.id}
                      tabIndex={0}
                      onClick={() => setSelectedId(record.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault()
                          setSelectedId(record.id)
                        }
                      }}
                      aria-selected={selectedId === record.id}
                      className={`cursor-pointer border-b border-line/60 transition-colors ${
                        selectedId === record.id ? "bg-accent-soft" : "hover:bg-canvas"
                      }`}
                    >
                      <Td mono>{fmtTime(record.created_at)}</Td>
                      <Td>
                        <span className="font-mono text-[13px] text-ink">
                          {record.chosen_model ?? "—"}
                        </span>
                        {record.decision_source === "fallback" && (
                          <span className="ml-1.5 text-chip-amber-text">(fallback)</span>
                        )}
                      </Td>
                      <Td mono>{record.profile}</Td>
                      <Td>{record.reasoning_level}</Td>
                      <Td>
                        <Chip
                          tone={record.decision_source === "clef" ? "gray" : "amber"}
                          label={record.decision_source}
                        />
                      </Td>
                      <Td mono>
                        {hasTimings
                          ? `${fmtTokens(record.prompt_tokens)} / ${fmtTokens(record.completion_tokens)}`
                          : "en curso…"}
                      </Td>
                      <Td>
                        {fallbacks.length === 0 ? (
                          <span className="text-faint">—</span>
                        ) : (
                          <span className="flex flex-wrap gap-1">
                            {fallbacks.map((attempt, index) => (
                              <span
                                key={`${record.id}:${attempt.model}:${index}`}
                                className="rounded bg-chip-amber-bg px-1.5 py-0.5 font-mono text-[11px] text-chip-amber-text"
                              >
                                {attempt.model} · {attempt.outcome}
                                {attempt.http_status != null ? ` (${attempt.http_status})` : ""}
                              </span>
                            ))}
                          </span>
                        )}
                      </Td>
                      <Td mono className="text-right">
                        {fmtSeconds(record.clef_latency_ms)}
                      </Td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="Petición seleccionada">
          {selected == null ? (
            <EmptyNote>
              Selecciona una fila de la tabla para ver su decisión de Clef.
            </EmptyNote>
          ) : (
            <SelectedPanel record={selected} />
          )}
        </Card>

        <Card title="Nota">
          <p className="text-xs leading-relaxed text-muted">
            Leyendo <code className="font-mono">/v1/stream</code> — cada decisión que el gateway
            registra aparece aquí sin recargar. Las decisiones con origen{" "}
            <code className="font-mono">fallback</code> son aquellas en las que Clef no respondió y
            el router falló abierto a su modelo por defecto: no llevan respuestas de Clef ni
            probabilidades.
          </p>
        </Card>
      </div>
    </section>
  )
}

/** The selected record: id, timestamp, Clef answers and x-nan-auto-* values. */
function SelectedPanel({ record }: { record: DecisionRecord }) {
  return (
    <div className="space-y-4">
      <div className="min-w-0">
        <p className="break-all font-mono text-[13px] text-ink">{record.id}</p>
        <p className="mt-0.5 text-xs text-faint">{fmtTime(record.created_at)}</p>
      </div>

      <div>
        <h3 className="mb-2 text-xs font-medium text-muted">Respuestas de Clef</h3>
        {record.answers.length === 0 ? (
          <EmptyNote>
            Sin respuestas de Clef — esta decisión no vino de Clef.
          </EmptyNote>
        ) : (
          <ul className="space-y-1.5">
            {record.answers.map((answer) => (
              <li key={answer.question_id}>
                <AnswerRow answer={answer} />
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-xs font-medium text-muted">Cabeceras del gateway</h3>
        <dl className="space-y-1 font-mono text-[13px]">
          <div className="flex flex-wrap gap-x-2">
            <dt className="text-accent">x-nan-auto-model</dt>
            <dd className="min-w-0 break-all text-body">{record.chosen_model ?? "—"}</dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt className="text-accent">x-nan-auto-decision-id</dt>
            <dd className="min-w-0 break-all text-body">{record.id}</dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt className="text-muted">perfil</dt>
            <dd className="min-w-0 break-all text-body">{record.profile}</dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt className="text-muted">razonamiento</dt>
            <dd className="min-w-0 break-all text-body">{record.reasoning_level}</dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt className="text-muted">latencia de Clef</dt>
            <dd className="min-w-0 break-all text-body">{fmtSeconds(record.clef_latency_ms)}</dd>
          </div>
        </dl>
      </div>
    </div>
  )
}

/**
 * One stored answer. The decision log keeps the chosen label and its
 * confidence, not the full distribution — so the teal bar honestly shows the
 * confidence of the answer, nothing else.
 */
function AnswerRow({ answer }: { answer: ClefAnswer }) {
  const confidence =
    typeof answer.confidence === "number" && Number.isFinite(answer.confidence)
      ? Math.max(0, Math.min(100, answer.confidence * 100))
      : null
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-40 shrink-0 truncate font-mono text-ink">{answer.question_id}</span>
      <span className="w-28 shrink-0 truncate text-body">{String(answer.value)}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-sm bg-canvas">
        {confidence != null && (
          <div className="h-full rounded-sm bg-accent" style={{ width: `${confidence}%` }} />
        )}
      </div>
      <span className="w-12 shrink-0 text-right font-mono tabular-nums text-muted">
        {confidence == null ? "—" : `${Math.round(confidence)}%`}
      </span>
    </div>
  )
}
