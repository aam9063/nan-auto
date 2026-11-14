import { useLiveFeed, type FeedStatus } from "../hooks/useLiveFeed"
import { fmtMs, fmtTime, fmtTokens } from "./format"
import { StatusBadge } from "./parts"
import type { ReactNode } from "react"

const STATUS_BADGE: Record<FeedStatus, { tone: "good" | "neutral" | "warn"; label: string }> = {
  connecting: { tone: "neutral", label: "connecting…" },
  live: { tone: "good", label: "live" },
  reconnecting: { tone: "warn", label: "stream down — retrying" },
}

export function LiveFeedScreen() {
  const { status, events, message } = useLiveFeed()

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge {...STATUS_BADGE[status]} />
        <span className="text-xs text-neutral-500">
          Reading <code>/v1/stream</code> — every decision the gateway records shows up here
          without reloading.
        </span>
        {events.length > 0 && (
          <span className="ml-auto text-xs text-neutral-500">
            {events.length} decision{events.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {message != null && (
        <p className="rounded-md border border-amber-800 bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
          {message}
        </p>
      )}

      {events.length === 0 ? (
        <p className="rounded-lg border border-dashed border-neutral-800 p-6 text-sm text-neutral-400">
          Nothing has been routed yet. Send a request through the gateway — for example from the
          Playground's “Send” button — and it will appear here as the gateway records it.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-800">
          <table className="w-full min-w-160 text-left text-xs">
            <thead>
              <tr className="border-b border-neutral-800 bg-neutral-900/60 text-neutral-500">
                <Th>time</Th>
                <Th>model</Th>
                <Th>profile</Th>
                <Th>reasoning</Th>
                <Th>source</Th>
                <Th>ttft</Th>
                <Th>total</Th>
                <Th>tokens (in/out)</Th>
                <Th>fallbacks</Th>
              </tr>
            </thead>
            <tbody>
              {events.map((record) => {
                const fallbacks = record.attempts.filter((attempt) => attempt.outcome !== "success")
                const hasTimings =
                  record.ttft_ms != null ||
                  record.total_ms != null ||
                  record.prompt_tokens != null ||
                  record.completion_tokens != null
                return (
                  <tr key={record.id} className="border-b border-neutral-800/60 text-neutral-300">
                    <Td mono>{fmtTime(record.created_at)}</Td>
                    <Td>
                      {record.chosen_model ?? "—"}
                      {record.decision_source === "fallback" && (
                        <span className="ml-1.5 text-amber-400">(fallback)</span>
                      )}
                    </Td>
                    <Td mono>{record.profile}</Td>
                    <Td>{record.reasoning_level}</Td>
                    <Td>
                      <StatusBadge
                        tone={record.decision_source === "clef" ? "neutral" : "warn"}
                        label={record.decision_source}
                      />
                    </Td>
                    <Td mono>{hasTimings ? fmtMs(record.ttft_ms) : "running…"}</Td>
                    <Td mono>{hasTimings ? fmtMs(record.total_ms) : "running…"}</Td>
                    <Td mono>
                      {hasTimings
                        ? `${fmtTokens(record.prompt_tokens)} / ${fmtTokens(record.completion_tokens)}`
                        : "running…"}
                    </Td>
                    <Td>
                      {fallbacks.length === 0 ? (
                        <span className="text-neutral-600">—</span>
                      ) : (
                        <span className="flex flex-wrap gap-1">
                          {fallbacks.map((attempt, index) => (
                            <span
                              key={`${record.id}:${attempt.model}:${index}`}
                              className="rounded bg-neutral-800 px-1.5 py-0.5 text-amber-300"
                            >
                              {attempt.model} · {attempt.outcome}
                              {attempt.http_status != null ? ` (${attempt.http_status})` : ""}
                            </span>
                          ))}
                        </span>
                      )}
                    </Td>
                  </tr>
                )
              })}
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
    <td className={`px-3 py-2 ${mono ? "tabular-nums" : ""} whitespace-nowrap`}>{children}</td>
  )
}
