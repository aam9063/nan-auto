/**
 * The evaluation screen, in its honest empty state.
 *
 * Spec 10 wants a Pareto chart of quality against latency, a baselines table
 * and a confusion matrix for the `task` question. None of that exists yet:
 * the gateway exposes `/v1/decisions`, `/v1/registry` and `/v1/stream`, and
 * none of them carries quality measurements or a baseline comparison. Until a
 * quality matrix is measured and served, this screen deliberately shows
 * nothing — a chart with made-up numbers would be worse than no chart.
 */
export function EvaluationScreen() {
  return (
    <section className="space-y-4">
      <div className="rounded-lg border border-dashed border-neutral-700 p-8">
        <h2 className="text-lg font-semibold text-neutral-200">No evaluation data yet</h2>
        <p className="mt-3 max-w-3xl text-sm text-neutral-400">
          This screen will show three things, none of which can be measured from the dashboard
          alone:
        </p>
        <ul className="mt-3 list-disc space-y-1.5 pl-6 text-sm text-neutral-400">
          <li>
            <span className="font-medium text-neutral-300">Pareto chart</span> — quality against
            latency, one point per model tier, so the trade-off the router is choosing on is
            visible.
          </li>
          <li>
            <span className="font-medium text-neutral-300">Baselines table</span> — the router's
            picks against a fixed reference (for example the largest model always).
          </li>
          <li>
            <span className="font-medium text-neutral-300">Task confusion matrix</span> — where
            Clef's `task` label disagrees with a known truth set.
          </li>
        </ul>
        <p className="mt-4 max-w-3xl text-sm text-neutral-400">
          It is waiting on a quality evaluation matrix that does not exist yet: the gateway serves
          no endpoint with quality measurements, and the dashboard will not draw a chart from
          invented numbers. When that matrix exists behind an API, this screen reads it; until
          then it stays empty, on purpose.
        </p>
      </div>
    </section>
  )
}
