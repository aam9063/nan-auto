import { useRegistry } from "../hooks/useRegistry"
import { Card, DecisionRefusal, EmptyNote, StateChip, Td, Th, TierChip, UiButton } from "./parts"
import type { RegistryModel } from "../types"

/** "sin dato" in place of every value the registry does not carry. */
const TOKEN_OR_SIN_DATO = (value: number | null) =>
  value == null ? "sin dato" : value.toLocaleString("es-ES")

const TOOLS_OR_SIN_DATO = (tools: boolean | string) => {
  if (typeof tools === "string") return tools
  return tools ? "sí" : "no"
}

const EXPLAINERS: { title: string; text: string }[] = [
  {
    title: "Circuito",
    text: "Cada modelo lleva su cortacircuitos: tras varios fallos seguidos el gateway deja de intentarlo un tiempo y pasa al siguiente candidato, en lugar de insistir contra un modelo caído.",
  },
  {
    title: "Sin cuota (402)",
    text: "Un 402 significa que el proveedor agotó la cuota del modelo. La fila lo marca como degradado y el router lo excluye hasta que caduca la marca «abierto hasta».",
  },
  {
    title: "Herramientas en XML",
    text: "Algunos modelos solo aceptan las herramientas descritas en XML. Cuando el cliente exige el formato nativo, el router los descarta antes de decidir (motivo «xml_tools»).",
  },
]

export function RegistryScreen() {
  const { status, models, error, refetch } = useRegistry()

  return (
    <section className="space-y-6">
      <Card
        title="Modelos de NaN"
        actions={
          <UiButton onClick={refetch} className="px-3 py-1.5 text-xs">
            Refrescar registro
          </UiButton>
        }
      >
        <EmptyNote>
          Leyendo <code className="font-mono">/v1/registry</code> — el inventario y su estado en
          vivo, tal como lo ve el gateway.
          {status === "ready" &&
            ` ${models.length} modelo${models.length === 1 ? "" : "s"} en el registro.`}
        </EmptyNote>

        <div className="mt-4">
          {status === "error" && (
            <DecisionRefusal title="Registro no disponible" message={error ?? "error desconocido"} />
          )}

          {status === "loading" && <EmptyNote>Cargando el registro…</EmptyNote>}

          {status === "ready" && models.length === 0 && (
            <div className="rounded-lg border border-dashed border-line p-6">
              <EmptyNote>
                El registro no informa de ningún modelo. No se inventa nada para rellenar la tabla.
              </EmptyNote>
            </div>
          )}

          {status === "ready" && models.length > 0 && (
            <div className="-mx-2 overflow-x-auto">
              <table className="w-full min-w-[960px] text-left text-sm">
                <thead>
                  <tr className="border-b border-line text-xs text-muted">
                    <Th>Modelo</Th>
                    <Th>Escalón</Th>
                    <Th>Contexto</Th>
                    <Th>Entrada</Th>
                    <Th>Herramientas</Th>
                    <Th>Razonamiento</Th>
                    <Th>Estado</Th>
                    <Th>Circuito</Th>
                    <Th>Latencia</Th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((model) => (
                    <RegistryRow key={model.id} model={model} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>

      <Card title="Cómo se lee">
        <dl className="grid gap-6 sm:grid-cols-3">
          {EXPLAINERS.map((explainer) => (
            <div key={explainer.title}>
              <dt className="text-sm font-semibold text-ink">{explainer.title}</dt>
              <dd className="mt-1 text-xs leading-relaxed text-muted">{explainer.text}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </section>
  )
}

function RegistryRow({ model }: { model: RegistryModel }) {
  const degraded = model.state.quota_exhausted_until != null
  return (
    <tr className="border-b border-line/60 last:border-b-0">
      <Td>
        <span className="font-mono text-[13px] font-medium text-ink">{model.id}</span>
        <span className="ml-2 text-xs text-faint">{model.owned_by}</span>
      </Td>
      <Td>{model.tier === "" ? <span className="text-faint">sin dato</span> : <TierChip tier={model.tier} />}</Td>
      <Td mono>{TOKEN_OR_SIN_DATO(model.max_input_tokens)}</Td>
      <Td mono>{model.input.join(", ")}</Td>
      <Td mono>{TOOLS_OR_SIN_DATO(model.tools)}</Td>
      <Td mono>{model.reasoning_mode}</Td>
      <Td>
        <StateChip available={model.state.available} degraded={degraded} />
        {model.state.failures > 0 && (
          <span className="ml-2 font-mono text-[11px] text-faint">
            {model.state.failures} fallos
          </span>
        )}
      </Td>
      <Td mono>
        {degraded ? (
          <span className="text-chip-amber-text">
            abierto hasta {model.state.quota_exhausted_until}
          </span>
        ) : (
          <span className="text-body">cerrado</span>
        )}
      </Td>
      <Td>
        <span className="text-faint">sin dato</span>
      </Td>
    </tr>
  )
}
