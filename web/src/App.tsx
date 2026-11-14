import { useEffect, useState } from "react"
import { EvaluationScreen } from "./components/EvaluationScreen"
import { LiveFeedScreen } from "./components/LiveFeedScreen"
import { PlaygroundScreen } from "./components/PlaygroundScreen"
import { RegistryScreen } from "./components/RegistryScreen"
import { ActivityIcon, ChartIcon, NodesIcon, PlayIcon, RowsIcon } from "./components/icons"
import { getDecisions } from "./api"

type Tab = "playground" | "feed" | "registry" | "evaluation"

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: "playground", label: "Playground", icon: <PlayIcon /> },
  { id: "feed", label: "Feed en vivo", icon: <ActivityIcon /> },
  { id: "registry", label: "Registro", icon: <RowsIcon /> },
  { id: "evaluation", label: "Evaluación", icon: <ChartIcon /> },
]

const TITLES: Record<Tab, string> = {
  playground: "Playground",
  feed: "Feed en vivo",
  registry: "Registro",
  evaluation: "Evaluación",
}

type GatewayHealth = "checking" | "online" | "down"

/**
 * The NaN Auto dashboard: sidebar shell over four screens, plain state-based
 * navigation (no router library needed). The API key lives in this
 * component's state only — typed once, kept in session memory, never
 * persisted anywhere.
 *
 * The sidebar footer shows live facts, nothing else: Clef's latency from the
 * most recent decision and whether the gateway answers right now (polled
 * every 10 s through the same read API the feed uses — `/healthz` itself is
 * not proxied by the Vite dev server).
 */
export default function App() {
  const [tab, setTab] = useState<Tab>("playground")
  const [apiKey, setApiKey] = useState("")
  const [health, setHealth] = useState<GatewayHealth>("checking")
  const [clefLatencyMs, setClefLatencyMs] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const { decisions } = await getDecisions(1)
        if (cancelled) return
        setHealth("online")
        setClefLatencyMs(decisions[0]?.clef_latency_ms ?? null)
      } catch {
        if (cancelled) return
        setHealth("down")
      }
    }
    void poll()
    const timer = window.setInterval(poll, 10_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  return (
    <div className="min-h-screen bg-canvas text-body">
      <div className="mx-auto flex max-w-[1440px] flex-col md:flex-row">
        <aside className="flex w-full shrink-0 flex-row items-center gap-3 border-b border-line bg-sidebar px-4 py-3 md:sticky md:top-0 md:h-screen md:w-[250px] md:flex-col md:items-stretch md:gap-0 md:border-r md:border-b-0 md:px-4 md:py-6">
          <div className="flex items-center gap-3 md:mb-8 md:px-2">
            <span className="flex size-10 items-center justify-center rounded-xl bg-accent">
              <NodesIcon />
            </span>
            <span className="text-base font-bold tracking-tight text-ink">
              NaN Auto
            </span>
          </div>
          <nav
            aria-label="Pantallas del dashboard"
            className="flex flex-1 gap-1 md:flex-col"
          >
            {TABS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => setTab(entry.id)}
                aria-current={tab === entry.id ? "page" : undefined}
                className={`flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                  tab === entry.id
                    ? "bg-accent-soft font-semibold text-accent"
                    : "text-body hover:bg-canvas hover:text-ink"
                }`}
              >
                {entry.icon}
                {entry.label}
              </button>
            ))}
          </nav>
          <footer className="hidden shrink-0 space-y-1.5 border-t border-line pt-4 font-mono text-xs text-muted md:block">
            {clefLatencyMs != null && (
              <p>Clef {(clefLatencyMs / 1000).toFixed(2).replace(".", ",")} s</p>
            )}
            <p>
              Gateway{" "}
              <span
                className={
                  health === "down"
                    ? "text-chip-red-text"
                    : health === "online"
                      ? "text-accent"
                      : "text-faint"
                }
              >
                {health === "down"
                  ? "sin conexión"
                  : health === "online"
                    ? "en línea"
                    : "comprobando…"}
              </span>
            </p>
          </footer>
        </aside>

        <main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">
          <p className="text-xs text-faint">Gateway · nan-auto</p>
          <h1 className="mb-6 mt-1 text-[28px] font-semibold tracking-tight text-ink">
            {TITLES[tab]}
          </h1>
          {tab === "playground" && (
            <PlaygroundScreen apiKey={apiKey} onApiKeyChange={setApiKey} />
          )}
          {tab === "feed" && <LiveFeedScreen />}
          {tab === "registry" && <RegistryScreen />}
          {tab === "evaluation" && <EvaluationScreen />}
        </main>
      </div>
    </div>
  )
}
