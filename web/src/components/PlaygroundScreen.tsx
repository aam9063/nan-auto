import { useState } from "react"
import { DecisionRefusal, StatusBadge } from "./parts"
import { ProbabilityBars } from "./ProbabilityBars"
import { fmtMs, fmtTokens } from "./format"
import { ApiError, decide, sendChat, type PlaygroundBody } from "../api"
import type { ChatDelivery, PlaygroundDecision } from "../types"

/** The profiles the gateway's decision table knows (router_core/capabilities.py). */
const PROFILES = ["nan-auto", "nan-auto-fast", "nan-auto-quality"] as const

/** Example prompts from the demo script (spec 17): a greeting and a hard bug. */
const EXAMPLES: { label: string; prompt: string }[] = [
  { label: "greeting", prompt: "Hello! Who are you?" },
  {
    label: "hard bug",
    prompt:
      "A race condition corrupts our job queue only when two workers resume from the same checkpoint within 50 ms; it never reproduces with one worker. Walk me through finding it and fixing it.",
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
      : "unexpected error"
}

export function PlaygroundScreen({ apiKey }: { apiKey: string }) {
  const [prompt, setPrompt] = useState("")
  const [profile, setProfile] = useState("")
  const [decideState, setDecideState] = useState<DecideState>({ phase: "idle" })
  const [sendState, setSendState] = useState<SendState>({ phase: "idle" })

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

  return (
    <section className="space-y-6">
      <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="Type a prompt for the router…"
          rows={4}
          className="block w-full resize-y rounded-md border border-neutral-700 bg-neutral-950 p-3 text-sm text-neutral-100 placeholder:text-neutral-500 focus:border-neutral-500 focus:outline-none"
        />
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <label htmlFor="profile" className="text-neutral-400">
            Profile
          </label>
          <select
            id="profile"
            value={profile}
            onChange={(event) => setProfile(event.target.value)}
            className="rounded-md border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-neutral-100 focus:border-neutral-500 focus:outline-none"
          >
            <option value="">gateway default (alias)</option>
            {PROFILES.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={runDecide}
            disabled={decideState.phase === "loading" || prompt.trim() === ""}
            className="rounded-md bg-sky-600 px-4 py-1.5 font-medium text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-neutral-700 disabled:text-neutral-400"
          >
            {decideState.phase === "loading" ? "Deciding…" : "Decide"}
          </button>
          <button
            type="button"
            onClick={runSend}
            disabled={sendState.phase === "loading" || prompt.trim() === "" || apiKey === ""}
            title={apiKey === "" ? "Type the API key in the header to send" : undefined}
            className="rounded-md border border-neutral-600 px-4 py-1.5 font-medium text-neutral-100 hover:border-neutral-400 disabled:cursor-not-allowed disabled:border-neutral-700 disabled:text-neutral-500"
          >
            {sendState.phase === "loading" ? "Sending…" : "Send"}
          </button>
          {EXAMPLES.map((example) => (
            <button
              key={example.label}
              type="button"
              onClick={() => setPrompt(example.prompt)}
              className="rounded-md border border-dashed border-neutral-700 px-2.5 py-1 text-xs text-neutral-400 hover:border-neutral-500 hover:text-neutral-200"
            >
              example: {example.label}
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-neutral-500">
          “Decide” runs the router's decision path only — no model is called.
          “Send” posts to <code>/v1/chat/completions</code> for a real answer.
        </p>
      </div>

      {decideState.phase === "error" && (
        <DecisionRefusal title="Decision failed" message={decideState.message} />
      )}

      {decision != null && (
        <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
          {decision.decision_source === "fallback" && (
            <p className="mb-3 rounded-md border border-amber-800 bg-amber-950/40 px-3 py-2 text-xs text-amber-300">
              Clef was unavailable: the router failed open to its default model. There are no
              probabilities to show for this request.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-neutral-400">Model elected</span>
            <span className="text-lg font-semibold text-neutral-100">{decision.model}</span>
            <span className="text-sm text-neutral-400">
              reasoning <span className="text-neutral-200">{decision.reasoning_level}</span>
            </span>
            <StatusBadge
              tone={decision.decision_source === "clef" ? "good" : "warn"}
              label={`source: ${decision.decision_source}`}
            />
          </div>
          <p className="mt-2 text-sm text-neutral-300">{decision.reason}</p>

          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                Candidates (inventory order)
              </h3>
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {decision.candidates.map((model) => (
                  <li
                    key={model}
                    className="rounded border border-neutral-700 px-2 py-0.5 text-xs text-neutral-300"
                  >
                    {model}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                Rejected
              </h3>
              {decision.rejected.length === 0 ? (
                <p className="mt-2 text-xs text-neutral-500">No candidate was rejected.</p>
              ) : (
                <ul className="mt-2 space-y-1">
                  {decision.rejected.map((refusal) => (
                    <li key={`${refusal.model}:${refusal.reason}`} className="text-xs">
                      <span className="text-neutral-300">{refusal.model}</span>
                      <span className="mx-1 text-neutral-600">·</span>
                      <code className="rounded bg-neutral-800 px-1.5 py-0.5 text-amber-300">
                        {refusal.reason}
                      </code>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Clef probabilities per question
          </h3>
          {probabilityEntries.length === 0 ? (
            <p className="mt-2 text-xs text-neutral-500">
              No probabilities: the decision did not come from Clef.
            </p>
          ) : (
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              {probabilityEntries.map(([questionId, answer_]) => (
                <ProbabilityBars key={questionId} questionId={questionId} answer={answer_} />
              ))}
            </div>
          )}
        </div>
      )}

      {sendState.phase === "error" && (
        <DecisionRefusal title="Send failed" message={sendState.message} />
      )}

      {delivery != null && (
        <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <StatusBadge tone="good" label={`HTTP ${delivery.status}`} />
            <span className="text-neutral-300">{fmtMs(delivery.latency_ms)} round trip</span>
          </div>
          <div className="mt-3 grid gap-3 text-xs md:grid-cols-3">
            <div>
              <span className="font-semibold uppercase tracking-wide text-neutral-500">
                Gateway headers
              </span>
              {Object.entries(delivery.headers).length === 0 ? (
                <p className="mt-1 text-neutral-500">None returned by this response.</p>
              ) : (
                <ul className="mt-1 space-y-1">
                  {Object.entries(delivery.headers).map(([name, value]) => (
                    <li key={name} className="break-all text-neutral-300">
                      <code className="text-amber-300">{name}</code> {value}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <span className="font-semibold uppercase tracking-wide text-neutral-500">Usage</span>
              <p className="mt-1 text-neutral-300">
                prompt {fmtTokens(delivery.body?.usage?.prompt_tokens)} · completion{" "}
                {fmtTokens(delivery.body?.usage?.completion_tokens)} · total{" "}
                {fmtTokens(delivery.body?.usage?.total_tokens)}
              </p>
            </div>
          </div>
          <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Answer
          </h3>
          <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-neutral-950 p-3 text-sm text-neutral-200">
            {answer == null || answer === "" ? "The response carried no content." : answer}
          </pre>
        </div>
      )}
    </section>
  )
}
