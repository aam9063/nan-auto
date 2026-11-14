// Typed client for the gateway's dashboard API (NAU-16b-T1).
//
// Every path is relative so the Vite dev proxy (web/vite.config.ts) forwards
// it to the gateway. Errors surface with their HTTP status and the server's
// own detail text, and every request carries an explicit timeout so the UI is
// never an endless spinner.
import type {
  ChatCompletionResponse,
  ChatDelivery,
  ChatMessage,
  DecisionRecord,
  DecisionsResponse,
  PlaygroundDecision,
  RegistryResponse,
} from "./types"

// A playground decide covers one clef question pass, so a few seconds are
// normal; a routed completion can legitimately take much longer.
const DECIDE_TIMEOUT_MS = 30_000
const CHAT_TIMEOUT_MS = 120_000
const READ_TIMEOUT_MS = 10_000

export class ApiError extends Error {
  /** 0 marks a transport failure (unreachable gateway or our own timeout). */
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

function timeoutMessage(timeoutMs: number): string {
  return `no answer within ${timeoutMs / 1000} s`
}

async function fetchJson<T>(path: string, init?: RequestInit, timeoutMs = READ_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(path, { ...init, signal: controller.signal })
  } catch (error) {
    const reason = error instanceof DOMException && error.name === "AbortError"
      ? timeoutMessage(timeoutMs)
      : "could not reach the gateway (is it running on :8000?)"
    throw new ApiError(0, reason)
  } finally {
    window.clearTimeout(timer)
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (body && typeof body.detail === "string") detail = `${response.status} — ${body.detail}`
    } catch {
      // Not JSON: the status line is all the honest detail there is.
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

/** The playground's chat-shaped body; `profile` is optional, as in routing. */
export interface PlaygroundBody {
  model: string
  messages: ChatMessage[]
  profile?: string
}

export function getDecisions(limit = 50): Promise<DecisionsResponse> {
  return fetchJson(`/v1/decisions?limit=${limit}`)
}

export function getDecision(id: string): Promise<DecisionRecord> {
  return fetchJson(`/v1/decisions/${encodeURIComponent(id)}`)
}

export function getRegistry(): Promise<RegistryResponse> {
  return fetchJson("/v1/registry")
}

export function decide(body: PlaygroundBody): Promise<PlaygroundDecision> {
  return fetchJson(
    "/v1/playground/decide",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    DECIDE_TIMEOUT_MS,
  )
}

// The gateway annotates every routed reply with four x-nan-auto-* headers
// (see proxy.py); those, and only those, are worth surfacing.
const NOTEWORTHY_HEADERS = [
  "x-nan-auto-model",
  "x-nan-auto-decision-id",
  "x-nan-auto-reason",
  "x-nan-auto-fallback",
] as const

/**
 * Send one real routed completion. The key lives only in this call's header,
 * in session memory — never in localStorage, never in any request body.
 */
export async function sendChat(apiKey: string, body: PlaygroundBody): Promise<ChatDelivery> {
  const started = performance.now()
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS)
  let response: Response
  try {
    response = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
  } catch (error) {
    const reason = error instanceof DOMException && error.name === "AbortError"
      ? timeoutMessage(CHAT_TIMEOUT_MS)
      : "could not reach the gateway (is it running on :8000?)"
    throw new ApiError(0, reason)
  } finally {
    window.clearTimeout(timer)
  }
  const headers: Record<string, string> = {}
  for (const name of NOTEWORTHY_HEADERS) {
    const value = response.headers.get(name)
    if (value !== null) headers[name] = value
  }
  const latency_ms = Math.round(performance.now() - started)
  if (!response.ok) {
    // The gateway relays the upstream status and body verbatim; show both.
    const text = await response.text()
    throw new ApiError(
      response.status,
      `${response.status} — ${text.trim().slice(0, 2000) || response.statusText}`,
    )
  }
  let parsed: ChatCompletionResponse | null = null
  try {
    parsed = (await response.json()) as ChatCompletionResponse
  } catch {
    parsed = null
  }
  return { status: response.status, latency_ms, headers, body: parsed }
}
