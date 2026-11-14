// Hand-written types for the gateway's dashboard API (NAU-16b-T1).
//
// The task calls for `openapi-typescript` types once the gateway is up
// (`pnpm --dir web api:types`); until the schema is generated, these are
// transcribed by hand from the server sources behind each contract:
//   - server/src/nan_auto/gateway/events.py      (decision_payload)
//   - server/src/nan_auto/gateway/decisions.py   (playground, decisions list)
//   - server/src/nan_auto/gateway/log_store.py   (answer / attempt rows)
//   - server/src/nan_auto/registry/router.py     (registry)
//   - server/src/nan_auto/registry/types.py      (ModelInfo fields)
//   - server/src/nan_auto/gateway/proxy.py       (chat completions relay)
// `null` always means "the gateway has no value yet" (e.g. a decision whose
// stream has not ended), never a client-side default.

export type ChatRole = "system" | "user" | "assistant"

export interface ChatMessage {
  role: ChatRole
  content: string
}

/** One clef answer: shapes mirror the store's rows (spec 9). */
export interface ClefAnswer {
  question_id: string
  label: string
  value: number | string | boolean
  confidence: number
}

/** One upstream attempt; `outcome` tags come from the store verbatim. */
export interface Attempt {
  model: string
  outcome: string
  http_status: number | null
  latency_ms: number | null
}

/** One decision, as the read API and the SSE stream both serialize it. */
export interface DecisionRecord {
  id: string
  profile: string
  chosen_model: string | null
  reasoning_level: string
  decision_source: string
  clef_timeout_ms: number
  clef_latency_ms: number
  ttft_ms: number | null
  total_ms: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  created_at: string | null
  answers: ClefAnswer[]
  attempts: Attempt[]
}

export interface DecisionsResponse {
  decisions: DecisionRecord[]
}

/** A choice question's probabilities, keyed by candidate label. */
export interface ChoiceProbabilities {
  label: string
  probabilities: Record<string, number>
  confidence: number
}

/** A score question's probabilities, keyed by scale level (stringified). */
export interface ScoreProbabilities {
  score: number
  probabilities: Record<string, number>
  confidence: number
}

/**
 * Per-question clef probabilities for the playground. On a clef failure the
 * gateway fails open and answers `{}` with `decision_source: "fallback"`, so
 * every question is optional by construction.
 */
export interface PlaygroundProbabilities {
  task?: ChoiceProbabilities
  difficulty?: ScoreProbabilities
  needs_reasoning?: { yes: number; confidence: number }
  expected_output_length?: ScoreProbabilities
}

/** Response of `POST /v1/playground/decide` (decide, without a model call). */
export interface PlaygroundDecision {
  model: string
  reasoning_level: string
  reason: string
  decision_source: "clef" | "fallback"
  candidates: string[]
  rejected: { model: string; reason: string }[]
  probabilities: PlaygroundProbabilities
}

/** One registry entry: inventory plus live state, never secrets. */
export interface RegistryModel {
  id: string
  object: "model"
  owned_by: string
  created: number
  chat: boolean
  mode: string
  input: string[]
  tools: boolean | string
  reasoning_mode: string
  premium: boolean
  tier: string
  max_input_tokens: number | null
  max_output_tokens: number | null
  state: {
    available: boolean
    failures: number
    quota_exhausted_until: string | null
  }
}

export interface RegistryResponse {
  object: "list"
  data: RegistryModel[]
}

/** The parts of an OpenAI chat-completions body the dashboard reads. */
export interface ChatCompletionResponse {
  choices?: { message?: { content?: string } }[]
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number }
  [key: string]: unknown
}

/** What "send" hands back: the exchange exactly as it happened. */
export interface ChatDelivery {
  status: number
  latency_ms: number
  headers: Record<string, string>
  body: ChatCompletionResponse | null // null when the body was not JSON
}
