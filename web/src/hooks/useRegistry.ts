import { useCallback, useEffect, useState } from "react"
import { getRegistry } from "../api"
import type { RegistryModel } from "../types"

export type RegistryStatus = "loading" | "ready" | "error"

export function useRegistry(): {
  status: RegistryStatus
  models: RegistryModel[]
  error: string | null
  refetch: () => void
} {
  const [status, setStatus] = useState<RegistryStatus>("loading")
  const [models, setModels] = useState<RegistryModel[]>([])
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    setStatus("loading")
    setError(null)
    getRegistry()
      .then((response) => {
        if (cancelled) return
        setModels(response.data)
        setStatus("ready")
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setError(
          cause instanceof Error ? cause.message : "unexpected error reading the registry",
        )
        setStatus("error")
      })
    return () => {
      cancelled = true
    }
  }, [attempt])

  return {
    status,
    models,
    error,
    refetch: useCallback(() => setAttempt((n) => n + 1), []),
  }
}
