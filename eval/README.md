# eval

Offline evaluation in RouterBench style (spec 11).

- `data/intents.jsonl` — labeled prompts with `task` and `difficulty` (NAU-14, to be built).
- `data/quality.parquet` — quality matrix: every prompt answered by every model, cached to disk.
- `bench/` — generator for the quality matrix (concurrency-limited, cached).
- `run.py` — evaluation of any policy against the cached matrix, no new calls.

Note: `run.py` raises until NAU-15 lands.
