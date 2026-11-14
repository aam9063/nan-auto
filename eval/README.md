# eval

Offline evaluation in RouterBench style (spec 11).

- `data/intents.jsonl` — the labelled prompt dataset (NAU-14): 28 prompts,
  Spanish and English, short and long, with and without code fences. Each line
  is `{"id", "prompt", "task", "difficulty"}` where `task` is one of the ten
  Clef questionnaire labels and `difficulty` is 0-4. It holds prompts only —
  no invented results.
- `data/quality.sqlite3` — the quality matrix cache: one row per
  (prompt_id, model) with latency, tokens, the answer text, and — after
  judging — `quality_score`, the raw `judge_raw` reply and `judge_model`.
- `bench/` — the generator (`bench/matrix.py`), the judge (`bench/judge.py`)
  and the evaluation logic (`bench/run.py`), importable as the `bench` package.
- `run.py` — CLI entry point; the logic lives in `bench/run.py` so the server
  tests can import it as `bench.run`.

## The quality matrix (NAU-14)

The generator asks every prompt of every chat model of the live catalog once,
with `temperature=0`, and caches each (prompt_id, model) answer in SQLite.
Calls are bounded by a concurrency limiter and a 429 triggers an exponential
backoff (0.5 s, 1 s, 2 s, ...). It is **resumable**: any (prompt, model) pair
the cache already holds is skipped, so relaunching never repeats work or
spends quota twice. Errored pairs are stored with the error text and are
retried on the next run; only rows with a real answer count as done work.

**The full matrix costs of the order of `prompts x models` paid calls. It is
never launched automatically: it is a human decision** (spec 11.6, Fase 0).
When you decide to run it:

```sh
uv run --project server python eval/run.py --dry-run    # the call plan, no calls
uv run --project server python eval/run.py --generate   # NETWORK: the matrix, resumable
```

If the run is interrupted, run the same `--generate` command again: the cache
skips everything already answered and continues where it stopped. Judge the
answers afterwards:

```sh
uv run --project server python eval/run.py --judge      # NETWORK: the judge
```

### Matrix parameters and honest deviations (measured 2026-11-14)

- The generation cap is **`max_tokens=4096` for every model, uniformly**, with
  a **per-call timeout floor of 240 s** (CLI overrides: `--max-tokens`,
  `--timeout`). In practice most models in the catalog exceed the cap freely
  (answers up to ~12.6k completion tokens were measured), but for reasoning-
  heavy defaults the cap does bind: `qwen3.6` burned the whole 1024-token cap
  of the first draft on thinking alone and answered `content: None`.
  `kimi-k3.1-pro` rejects any explicit `temperature` (even 0.01) with a 400,
  so the harness retries once without it; kimi rows therefore sample at
  provider-default sampling — a documented loss of determinism for that
  model only.
- Four hard cells (the deep-reasoning prompts the 4096 cap could not hold:
  2× qwen3.6 thinking prompts, 2× qwen3.8-max timeouts) were regenerated with
  `--max-tokens 8192 --timeout 480`. One cell remains unmeasured after every
  attempt (`en-016`, a maths word problem, on `qwen3.6`) even at 16 384
  tokens: the matrix is **307/308 measured** and the report states
  `complete: false` honestly. A first partial matrix at the 1024-token cap is
  archived as `data/quality-max1024.sqlite3.bak`.

## The judge (NAU-14-T4)

One judge model (`glm5.3`, the card's top tier) scores every stored answer on
a fixed, documented 0-4 scale (see `RUBRIC` in `bench/judge.py`): 0 unusable,
1 poor, 2 acceptable, 3 good, 4 excellent. The judge must reply with the exact
line `Score: N`; the raw reply is kept in `judge_raw` so any score can be
re-derived and audited. Known limitations: a single judge carries its own
family bias (scores are relative to this one judge), and the 10% human
re-review is out of scope for this task.

## Evaluating policies (NAU-15)

```sh
uv run --project server python eval/run.py
```

reads only the cache (no network) and reports, per policy, mean quality, mean
latency and honest coverage, plus the Pareto points (one per unique model and
one per policy) as JSON and a readable table:

- **R0** — always `deepseek-v4-flash` (the profile default, spec 11.4).
- **R1** — always the most capable measured model (card tier `t3_max`, degrading
  tier by tier when the top tier was never measured).
- **R2** — always the fastest measured model (lowest mean latency).
- **R3** — the router policy: for each prompt, the policy decides offline which
  model it would pick (labels from the dataset stand in for Clef, with full
  confidence and `needs_reasoning` derived from difficulty), and its cached row
  is read.

With an incomplete matrix the report says so and computes over the cached rows
only; the sample size is always stated (`note` in the JSON). The intended
visualization (spec 11.5) plots latency on X and quality on Y, one point per
model, with the router expected above the line joining the models.
