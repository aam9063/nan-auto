import { useEffect, useState } from "react"
import { getDecisions } from "../api"
import type { DecisionRecord } from "../types"

export type FeedStatus = "connecting" | "live" | "reconnecting"

export interface LiveFeed {
  status: FeedStatus
  events: DecisionRecord[]
  message: string | null
}

/**
 * One `EventSource` over `GET /v1/stream` (frames: `event: decision` with the
 * same JSON payload the read API returns). The browser reconnects on its own,
 * so an unreachable stream is a state to display, not an error to abandon.
 * The list is seeded with recently stored decisions so the screen is honest
 * before live traffic arrives.
 */
export function useLiveFeed(limit = 50): LiveFeed {
  const [status, setStatus] = useState<FeedStatus>("connecting")
  const [events, setEvents] = useState<DecisionRecord[]>([])
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    let closed = false
    const source = new EventSource("/v1/stream")
    source.addEventListener("open", () => {
      if (closed) return
      setStatus("live")
      setMessage(null)
    })
    source.addEventListener("error", () => {
      if (closed) return
      setStatus("reconnecting")
      setMessage("The gateway's stream is unreachable; the browser keeps retrying. Is the gateway running on :8000?")
    })
    source.addEventListener("decision", (event) => {
      if (closed) return
      try {
        const record = JSON.parse((event as MessageEvent<string>).data) as DecisionRecord
        setStatus("live")
        setMessage(null)
        setEvents((prior) => mergeDecision(prior, record))
      } catch {
        // A malformed frame is dropped; showing nothing beats showing fiction.
      }
    })
    getDecisions(limit)
      .then((result) => {
        if (!closed) setEvents((prior) => result.decisions.reduce(mergeDecision, prior))
      })
      .catch(() => {
        // History is optional; the live stream is what the feed is for.
      })
    return () => {
      closed = true
      source.close()
    }
  }, [limit])

  return { status, events, message }
}

/** Newest first, deduplicated by id (a stored decision may also arrive live). */
function mergeDecision(list: DecisionRecord[], record: DecisionRecord): DecisionRecord[] {
  const index = list.findIndex((item) => item.id === record.id)
  if (index === -1) return [record, ...list].slice(0, 200)
  const next = list.slice()
  next[index] = record
  return next
}
