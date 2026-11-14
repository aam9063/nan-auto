import { useRegistry } from "../hooks/useRegistry"
import { DecisionRefusal, StatusBadge } from "./parts"
import type { ReactNode } from "react"

const TOKEN_OR_DASH = (value: number | null) => (value == null ? "—" : value.toLocaleString())

export function RegistryScreen() {
  const { status, models, error, refetch } = useRegistry()

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-neutral-500">
          Reading <code>/v1/registry</code> — inventory plus live state, as the gateway sees it.
        </span>
        {status === "ready" && (
          <span className="ml-auto text-xs text-neutral-500">
            {models.length} model{models.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {status === "error" && (
        <div className="space-y-2">
          <DecisionRefusal title="Registry unavailable" message={error ?? "unknown error"} />
          <button
            type="button"
            onClick={refetch}
            className="rounded-md border border-neutral-600 px-3 py-1 text-sm text-neutral-100 hover:border-neutral-400"
          >
            Retry
          </button>
        </div>
      )}

      {status === "loading" && (
        <p className="rounded-lg border border-dashed border-neutral-800 p-6 text-sm text-neutral-400">
          Loading the registry…
        </p>
      )}

      {status === "ready" && models.length === 0 && (
        <p className="rounded-lg border border-dashed border-neutral-800 p-6 text-sm text-neutral-400">
          The registry reports no models. Nothing has been invented to fill the table.
        </p>
      )}

      {status === "ready" && models.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-neutral-800">
          <table className="w-full min-w-200 text-left text-xs">
            <thead>
              <tr className="border-b border-neutral-800 bg-neutral-900/60 text-neutral-500">
                <Th>model</Th>
                <Th>tier</Th>
                <Th>state</Th>
                <Th>failures</Th>
                <Th>quota</Th>
                <Th>mode</Th>
                <Th>input</Th>
                <Th>tools</Th>
                <Th>reasoning</Th>
                <Th>premium</Th>
                <Th>max in/out</Th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model.id} className="border-b border-neutral-800/60 text-neutral-300">
                  <Td>
                    <span className="font-medium text-neutral-100">{model.id}</span>
                    <span className="ml-2 text-neutral-500">{model.owned_by}</span>
                  </Td>
                  <Td mono>{model.tier === "" ? "—" : model.tier}</Td>
                  <Td>
                    <StatusBadge
                      tone={model.state.available ? "good" : "bad"}
                      label={model.state.available ? "available" : "unavailable"}
                    />
                  </Td>
                  <Td mono>{model.state.failures}</Td>
                  <Td>
                    {model.state.quota_exhausted_until == null ? (
                      <span className="text-neutral-600">ok</span>
                    ) : (
                      <span className="text-amber-300">
                        until {model.state.quota_exhausted_until}
                      </span>
                    )}
                  </Td>
                  <Td mono>{model.mode}</Td>
                  <Td mono>{model.input.join(", ")}</Td>
                  <Td mono>{String(model.tools)}</Td>
                  <Td mono>{model.reasoning_mode}</Td>
                  <Td mono>{model.premium ? "yes" : "no"}</Td>
                  <Td mono>
                    {TOKEN_OR_DASH(model.max_input_tokens)} / {TOKEN_OR_DASH(model.max_output_tokens)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Th({ children }: { children: string }) {
  return <th className="px-3 py-2 font-medium uppercase tracking-wide">{children}</th>
}

function Td({
  children,
  mono = false,
}: {
  children: ReactNode
  mono?: boolean
}) {
  return (
    <td className={`px-3 py-2 whitespace-nowrap ${mono ? "tabular-nums" : ""}`}>{children}</td>
  )
}
