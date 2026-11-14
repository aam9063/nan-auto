import { useState } from "react"
import { EvaluationScreen } from "./components/EvaluationScreen"
import { LiveFeedScreen } from "./components/LiveFeedScreen"
import { PlaygroundScreen } from "./components/PlaygroundScreen"
import { RegistryScreen } from "./components/RegistryScreen"

type Tab = "playground" | "feed" | "registry" | "evaluation"

const TABS: { id: Tab; label: string }[] = [
  { id: "playground", label: "Playground" },
  { id: "feed", label: "Live feed" },
  { id: "registry", label: "Registry" },
  { id: "evaluation", label: "Evaluation" },
]

/**
 * The NaN Auto dashboard: four screens, plain state-based navigation (no
 * router library needed). The API key lives in this component's state only —
 * typed once, kept in session memory, never persisted anywhere.
 */
export default function App() {
  const [tab, setTab] = useState<Tab>("playground")
  const [apiKey, setApiKey] = useState("")

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-200">
      <header className="border-b border-neutral-800 bg-neutral-900/40">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-6 py-4">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-neutral-100">NaN Auto</h1>
            <p className="text-xs text-neutral-500">
              One OpenAI-compatible endpoint. Clef picks the model, per request.
            </p>
          </div>
          <nav className="flex gap-1" aria-label="Dashboard screens">
            {TABS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => setTab(entry.id)}
                aria-current={tab === entry.id ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                  tab === entry.id
                    ? "bg-neutral-800 font-medium text-neutral-100"
                    : "text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200"
                }`}
              >
                {entry.label}
              </button>
            ))}
          </nav>
          <div className="ml-auto">
            <label htmlFor="api-key" className="block text-right text-xs text-neutral-500">
              API key
              <span aria-hidden className="ml-1">
                {apiKey === "" ? "○" : "●"}
              </span>
            </label>
            <input
              id="api-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="paste once — memory only"
              autoComplete="off"
              className="mt-1 w-56 rounded-md border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-sm text-neutral-100 placeholder:text-neutral-600 focus:border-neutral-500 focus:outline-none"
            />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-6">
        <p className="mb-4 text-xs text-neutral-600">
          The key is kept in this session's memory only — never in localStorage — and is used solely
          for “Send”, which posts to <code>/v1/chat/completions</code> with it as a bearer token.
        </p>
        {tab === "playground" && <PlaygroundScreen apiKey={apiKey} />}
        {tab === "feed" && <LiveFeedScreen />}
        {tab === "registry" && <RegistryScreen />}
        {tab === "evaluation" && <EvaluationScreen />}
      </main>
    </div>
  )
}
