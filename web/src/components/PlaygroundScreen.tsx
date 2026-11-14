import { useEffect, useState, type ReactElement } from "react"
import { Card, Chip, CodeStrip, DecisionRefusal, EmptyNote, TierChip, UiButton } from "./parts"
import { ProbabilityBars } from "./ProbabilityBars"
import { fmtMs, fmtTokens } from "./format"
import { ApiError, decide, getRegistry, sendChat, type PlaygroundBody } from "../api"
import type { ChatDelivery, PlaygroundDecision } from "../types"

/** The profiles the gateway's decision table knows (router_core/capabilities.py). */
const PROFILES = ["nan-auto", "nan-auto-fast", "nan-auto-quality"] as const

/** Example prompts for the quick tests: a greeting, a hard bug and a word problem. */
const QUICK_TESTS: { label: string; prompt: string }[] = [
  { label: "Saludo", prompt: "¡Hola! ¿Quién eres y qué sabes hacer?" },
  {
    label: "Bug difícil",
    prompt:
      "Una condición de carrera corrompe nuestra cola de trabajos solo cuando dos workers reanudan desde el mismo checkpoint con menos de 50 ms de diferencia; con un solo worker nunca se reproduce. Guíame para encontrarla y arreglarla.",
  },
  {
    label: "Problema de mates",
    prompt:
      "Un tren sale de Zaragoza hacia Barcelona a 90 km/h y, media hora después, otro sale de Barcelona hacia Zaragoza a 120 km/h. La distancia es de 300 km. ¿En cuánto se cruzan?",
  },
]

type DecideState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "done"; decision: PlaygroundDecision }
  | { phase: "error"; message: string }

type SendState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "done"; delivery: ChatDelivery }
  | { phase: "error"; message: string }

function message(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : error instanceof Error
      ? error.message
      : "error inesperado"
}

/** Segmented profile selector: pill container, selected segment in ink. */
function ProfileSegmented({
  value,
  onChange,
}: {
  value: string
  onChange: (profile: string) => void
}) {
  const options: { value: string; label: string }[] = [
    { value: "", label: "por defecto" },
    ...PROFILES.map((name) => ({ value: name, label: name })),
  ]
  return (
    <div
      role="group"
      aria-label="Perfil del router"
      className="inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-full border border-line bg-surface p-1"
    >
      {options.map((option) => (
        <button
          key={option.value || "default"}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={`whitespace-nowrap rounded-full px-3 py-1 font-mono text-xs transition-colors ${
            value === option.value
              ? "bg-ink font-semibold text-white"
              : "text-ink hover:bg-canvas"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

function SkeletonLine({ wide = "w-40" }: { wide?: string }) {
  return <div className={`animate-pulse rounded bg-canvas ${wide} h-4`} aria-hidden />
}

export function PlaygroundScreen({
  apiKey,
  onApiKeyChange,
}: {
  apiKey: string
  onApiKeyChange: (key: string) => void
}) {
  const [prompt, setPrompt] = useState("")
  const [profile, setProfile] = useState("")
  const [decideState, setDecideState] = useState<DecideState>({ phase: "idle" })
  const [sendState, setSendState] = useState<SendState>({ phase: "idle" })
  // Tier labels come from the registry — the decide payload does not carry a
  // tier, so the chip appears only for models the registry describes.
  const [tiers, setTiers] = useState<Record<string, string>>({})

  useEffect(() => {
    let cancelled = false
    getRegistry()
      .then((response) => {
        if (cancelled) return
        setTiers(Object.fromEntries(response.data.map((model) => [model.id, model.tier])))
      })
      .catch(() => {
        // Without the registry there is simply no tier chip — never a guess.
      })
    return () => {
      cancelled = true
    }
  }, [])

  const body: PlaygroundBody = {
    model: "nan-auto",
    messages: [{ role: "user", content: prompt }],
    ...(profile ? { profile } : {}),
  }

  async function runDecide() {
    setDecideState({ phase: "loading" })
    try {
      const decision = await decide(body)
      setDecideState({ phase: "done", decision })
    } catch (error) {
      setDecideState({ phase: "error", message: message(error) })
    }
  }

  async function runSend() {
    setSendState({ phase: "loading" })
    try {
      const delivery = await sendChat(apiKey, body)
      setSendState({ phase: "done", delivery })
    } catch (error) {
      setSendState({ phase: "error", message: message(error) })
    }
  }

  const decision = decideState.phase === "done" ? decideState.decision : null
  const probabilities = decision?.probabilities ?? {}
  const probabilityEntries = Object.entries(probabilities)
  const delivery = sendState.phase === "done" ? sendState.delivery : null
  const answer = delivery?.body?.choices?.[0]?.message?.content
  const reasonText =
    decision == null
      ? null
      : (delivery?.headers["x-nan-auto-reason"] ?? decision.reason)

  return (
    <section className="grid items-start gap-6 lg:grid-cols-[2fr_3fr]">
      {/* --- left column: the request ----------------------------------- */}
      <div className="space-y-6">
        <Card title="Petición">
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
            }}
          >
            <label htmlFor="playground-prompt" className="block text-sm font-medium text-ink">
              Mensaje
            </label>
            <textarea
              id="playground-prompt"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Escribe aquí el prompt para el router…"
              rows={5}
              className="block w-full resize-y rounded-lg border border-line bg-canvas p-3 text-sm text-ink placeholder:text-faint focus:border-accent focus:outline-none"
            />

            <div className="space-y-1.5">
              <span className="block text-sm font-medium text-ink">Perfil</span>
              <ProfileSegmented value={profile} onChange={setProfile} />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="playground-api-key" className="block text-sm font-medium text-ink">
                API key
              </label>
              <input
                id="playground-api-key"
                type="password"
                value={apiKey}
                onChange={(event) => onApiKeyChange(event.target.value)}
                placeholder="pegar una vez — solo en memoria"
                autoComplete="off"
                className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-faint focus:border-accent focus:outline-none"
              />
              <p className="text-xs text-faint">
                La clave se guarda solo en la memoria de esta sesión — nunca en localStorage — y
                únicamente la usa «Enviar y ver la respuesta», que hace POST a{" "}
                <code className="font-mono">/v1/chat/completions</code> con ella como bearer token.
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <UiButton
                variant="filled"
                onClick={runDecide}
                disabled={decideState.phase === "loading" || prompt.trim() === ""}
              >
                {decideState.phase === "loading" ? "Decidiendo…" : "Decidir modelo"}
              </UiButton>
              <UiButton
                onClick={runSend}
                disabled={sendState.phase === "loading" || prompt.trim() === "" || apiKey === ""}
                title={apiKey === "" ? "Escribe la API key para enviar" : undefined}
              >
                {sendState.phase === "loading" ? "Enviando…" : "Enviar y ver la respuesta"}
              </UiButton>
            </div>
            <p className="text-xs text-faint">
              «Decidir modelo» ejecuta solo la decisión del router — no se llama a ningún modelo.
              «Enviar y ver la respuesta» llama de verdad al modelo elegido.
            </p>
          </form>
        </Card>

        <Card title="Pruebas rápidas">
          <div className="flex flex-wrap gap-2">
            {QUICK_TESTS.map((quick) => (
              <UiButton
                key={quick.label}
                onClick={() => setPrompt(quick.prompt)}
                className="px-3 py-1.5 text-xs"
              >
                {quick.label}
              </UiButton>
            ))}
          </div>
          <p className="mt-3 text-xs text-faint">
            Rellenan el campo «Mensaje» con un ejemplo listo para decidir.
          </p>
        </Card>
      </div>

      {/* --- right column: the decision, Clef, candidates and answer ---- */}
      <div className="space-y-6">
        <Card title="Modelo elegido">
          {decideState.phase === "error" ? (
            <DecisionRefusal title="Falló la decisión" message={decideState.message} />
          ) : decideState.phase === "loading" ? (
            <div className="space-y-2">
              <SkeletonLine wide="w-64" />
              <SkeletonLine wide="w-96" />
            </div>
          ) : decision == null ? (
            <EmptyNote>
              Pulsa «Decidir modelo» y aquí aparecerá la elección del router.
            </EmptyNote>
          ) : (
            <>
              {decision.decision_source === "fallback" && (
                <p className="mb-3 rounded-lg bg-chip-amber-bg px-3 py-2 text-xs text-chip-amber-text">
                  Clef no estaba disponible: el router falló abierto a su modelo por defecto. No hay
                  probabilidades que mostrar para esta petición.
                </p>
              )}
              <p className="font-mono text-2xl font-semibold break-all text-ink">
                {decision.model}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {tiers[decision.model] != null && tiers[decision.model] !== "" && (
                  <TierChip tier={tiers[decision.model]} />
                )}
                <Chip
                  tone="gray"
                  label={`razonamiento: ${decision.reasoning_level}`}
                />
                <StatusChip source={decision.decision_source} />
              </div>
              {reasonText != null && reasonText !== "" && (
                <div className="mt-3">
                  <CodeStrip>{reasonText}</CodeStrip>
                </div>
              )}
            </>
          )}
        </Card>

        <Card title="Lo que dijo Clef">
          {decideState.phase === "loading" ? (
            <SkeletonLine wide="w-full" />
          ) : decision == null ? (
            <EmptyNote>Sin decisión todavía — Clef aún no ha respondido a nada.</EmptyNote>
          ) : probabilityEntries.length === 0 ? (
            <EmptyNote>
              Sin probabilidades: la decisión no ha venido de Clef.
            </EmptyNote>
          ) : (
            <div className="grid gap-2 md:grid-cols-2">
              {probabilityEntries.map(([questionId, answer_]) => (
                <ProbabilityBars key={questionId} questionId={questionId} answer={answer_} />
              ))}
            </div>
          )}
        </Card>

        <Card title="Candidatos">
          {decision == null ? (
            <EmptyNote>La lista de candidatos aparece con la primera decisión.</EmptyNote>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-line text-xs text-muted">
                      <th className="px-3 py-2 font-medium">Modelo</th>
                      <th className="px-3 py-2 font-medium">Escalón</th>
                      <th className="px-3 py-2 text-right font-medium">Puntuación</th>
                      <th className="px-3 py-2 font-medium">Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decision.candidates.map((model) => {
                      const rejected = decision.rejected.find((refusal) => refusal.model === model)
                      let status: ReactElement
                      if (model === decision.model) {
                        status = <Chip tone="teal" label="Elegido" />
                      } else if (rejected != null) {
                        status = (
                          <Chip tone="gray" label={`Descartado: ${rejected.reason}`} />
                        )
                      } else {
                        status = <Chip tone="gray" label="Considerado" />
                      }
                      return (
                        <tr key={model} className="border-b border-line/60 last:border-b-0">
                          <td className="px-3 py-2 font-mono text-[13px] text-ink">{model}</td>
                          <td className="px-3 py-2">
                            {tiers[model] != null && tiers[model] !== "" ? (
                              <TierChip tier={tiers[model]} />
                            ) : (
                              <span className="text-faint">sin dato</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-right font-mono tabular-nums text-muted">
                            —
                          </td>
                          <td className="px-3 py-2">{status}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-xs text-faint">
                El endpoint de decisión no expone la puntuación de cada candidato, así que esa
                columna queda honestamente vacía.
              </p>
            </>
          )}
        </Card>

        <Card title="Respuesta">
          {sendState.phase === "error" ? (
            <DecisionRefusal title="Falló el envío" message={sendState.message} />
          ) : sendState.phase === "loading" ? (
            <SkeletonLine wide="w-full" />
          ) : delivery == null ? (
            <EmptyNote>
              Escribe la API key, pulsa «Enviar y ver la respuesta» y aquí aparecerá la respuesta
              del modelo.
            </EmptyNote>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
                <Chip tone="teal" label={`HTTP ${delivery.status}`} />
                <Chip tone="gray" label={`total ${fmtMs(delivery.latency_ms)}`} />
                <Chip
                  tone="gray"
                  label={`tokens ${fmtTokens(delivery.body?.usage?.prompt_tokens)} / ${fmtTokens(delivery.body?.usage?.completion_tokens)}`}
                />
              </div>
              {Object.entries(delivery.headers).length > 0 && (
                <dl className="mt-3 space-y-1 text-xs">
                  {Object.entries(delivery.headers).map(([name, value]) => (
                    <div key={name} className="flex flex-wrap gap-x-2">
                      <dt className="font-mono text-accent">{name}</dt>
                      <dd className="min-w-0 break-all font-mono text-body">{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
              <div className="mt-3 rounded-lg bg-canvas p-3 text-sm whitespace-pre-wrap text-ink">
                {answer == null || answer === "" ? "La respuesta no llevó contenido." : answer}
              </div>
            </>
          )}
        </Card>
      </div>
    </section>
  )
}

function StatusChip({ source }: { source: PlaygroundDecision["decision_source"] }) {
  return source === "clef" ? (
    <Chip tone="teal" label="clef" />
  ) : (
    <Chip tone="amber" label="fallback" />
  )
}
