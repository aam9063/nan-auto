"""Offline policy evaluation (NAU-15, spec 11.2 and 11.5).

Baselines and Pareto data computed purely from the cached quality matrix:

- R0: always `deepseek-v4-flash` (the profile default, spec 11.4).
- R1: always the most capable model of the card's tier ladder that was measured.
- R2: always the fastest model as measured (lowest mean latency).
- R3: the router policy — for each prompt, the policy decides offline which
  model it would pick, and its cached row is read. No network.

With a complete matrix, this module never calls the network: the evaluation
is arithmetic over the cache. `--dry-run` only prints the call plan. The live
generation (`--generate`) and judging (`--judge`) are the two network modes,
and the full matrix run is reserved for an explicit human decision.

The CLI entry point is `eval/run.py`; this module holds the logic so the
server tests can import it as `bench.run`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import httpx  # noqa: E402

from bench import judge as bench_judge  # noqa: E402
from bench import matrix as bench_matrix  # noqa: E402
from bench.cache import all_rows, open_db  # noqa: E402
from nan_auto.clef_client.types import (  # noqa: E402
    ChoiceAnswer,
    Decision,
    NoulAnswer,
    ScoreAnswer,
)
from nan_auto.registry.catalog import load_card  # noqa: E402
from nan_auto.router_core.features import RequestFeatures  # noqa: E402
from nan_auto.router_core.policy import decide  # noqa: E402

DATA_DIR = EVAL_DIR / "data"
DEFAULT_DB = DATA_DIR / "quality.sqlite3"
DEFAULT_INTENTS = DATA_DIR / "intents.jsonl"

# R0 (spec 11.4): the profile default; mirrors Settings.default_model.
R0_MODEL = "deepseek-v4-flash"
# The card's tier ladder, most capable last; R1 reads it from the top.
_TIER_ORDER = ("t1_fast", "t2_generalist", "t3_max")


def tiers_from_card() -> dict[str, str]:
    """model_id -> tier, straight from `config/models.yaml` (no network)."""
    mapping: dict[str, str] = {}
    for tier, model_ids in load_card().tiers.items():
        for model_id in model_ids:
            mapping[model_id] = tier
    return mapping


def _inventory(model_ids: Iterable[str], tiers: Mapping[str, str]) -> dict[str, object]:
    """Minimal ModelInfo per cached model so the policy can rank it offline."""
    from nan_auto.registry.types import ModelInfo

    card = load_card()
    inventory: dict[str, object] = {}
    for model_id in model_ids:
        entry = card.models.get(model_id, {})
        reasoning = entry.get("reasoning")
        reasoning = reasoning if isinstance(reasoning, dict) else {}
        entry_tier = entry.get("tier", "") if isinstance(entry.get("tier"), str) else ""
        inventory[model_id] = ModelInfo(
            id=model_id,
            mode="chat",
            tier=tiers.get(model_id, "") or entry_tier,
            reasoning_mode=reasoning.get("mode", "unknown"),
            reasoning_off=reasoning.get("off", {}) if isinstance(reasoning.get("off"), dict) else {},  # noqa: E501
            reasoning_on=reasoning.get("on", {}) if isinstance(reasoning.get("on"), dict) else {},
        )
    return inventory


def _decision_for(intent: bench_matrix.Intent) -> Decision:
    """A deterministic Decision built from the dataset's own labels.

    Clef is not called: the benchmark's labels stand in for the live
    classification, with certainty everywhere (confidence 1.0, no split
    difficulty), so the replay is fully reproducible. `needs_reasoning` is
    derived from the labelled difficulty (hard tasks need reasoning).
    """
    return Decision(
        decision_id=f"eval-{intent.id}",
        model="eval",
        task=ChoiceAnswer(label=intent.task, probabilities={intent.task: 1.0}, confidence=1.0),
        difficulty=ScoreAnswer(
            score=float(intent.difficulty),
            legend={},
            probabilities={str(intent.difficulty): 1.0},
            confidence=1.0,
        ),
        needs_reasoning=NoulAnswer(yes=1.0 if intent.difficulty >= 3 else 0.0, confidence=1.0),
        expected_output_length=ScoreAnswer(score=0.0, legend={}, probabilities={}, confidence=1.0),
    )


def _features_for(intent: bench_matrix.Intent) -> RequestFeatures:
    from nan_auto.clef_client.questions import estimate_tokens

    return RequestFeatures(
        est_tokens=estimate_tokens(intent.prompt),
        turns=1,
        has_code_fences="```" in intent.prompt,
    )


def _most_capable(measured: Iterable[str], tiers: Mapping[str, str]) -> str | None:
    """The top-tier measured model; the ladder degrades tier by tier."""
    by_tier: dict[str, list[str]] = {tier: [] for tier in _TIER_ORDER}
    for model_id in measured:
        tier = tiers.get(model_id, "")
        by_tier.setdefault(tier, []).append(model_id)
    for tier in reversed(_TIER_ORDER):
        candidates = sorted(by_tier.get(tier, []))
        if candidates:
            return candidates[0]
    return min(measured) if measured else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _aggregate(rows, total_prompts: int) -> dict:
    """One policy/model point: mean quality, mean latency, honest coverage."""
    scored = [row.quality_score for row in rows if row.quality_score is not None]
    latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
    answered = [row for row in rows if row.answer is not None]
    return {
        "quality": _mean([float(score) for score in scored]),
        "latency_ms": _mean(latencies),
        "coverage": len(answered) / total_prompts if total_prompts else 0.0,
        "scored_prompts": len(scored),
    }


def evaluate(
    db, intents: Sequence[bench_matrix.Intent], *, tiers: Mapping[str, str] | None = None
) -> dict:
    """Baselines R0-R3 plus Pareto data, purely from the cache."""
    tier_map = tiers if tiers is not None else tiers_from_card()
    rows = all_rows(db)
    by_pair = {(row.prompt_id, row.model): row for row in rows}
    measured = sorted({row.model for row in rows if row.answer is not None})

    def rows_for(model: str) -> list:
        return [
            by_pair[(intent.id, model)]
            for intent in intents
            if (intent.id, model) in by_pair
        ]

    def static_point(model: str | None) -> dict:
        point = _aggregate(rows_for(model) if model else [], len(intents))
        point["model"] = model
        return point

    # R3: ask the policy which model it would pick for each prompt, offline.
    inventory = _inventory(measured, tier_map)
    choices: dict[str, str | None] = {}
    r3_rows = []
    for intent in intents:
        result = decide(
            _decision_for(intent),
            _features_for(intent),
            inventory,  # type: ignore[arg-type]
            default_model=None,
        )
        model = result.model
        choices[intent.id] = model
        if model is not None and (intent.id, model) in by_pair:
            r3_rows.append(by_pair[(intent.id, model)])

    baselines = {
        "R0": static_point(R0_MODEL if R0_MODEL in measured else None),
        "R1": static_point(_most_capable(measured, tier_map)),
        "R2": static_point(_fastest(measured, rows)),
        "R3": {
            "model": "router",
            **_aggregate(r3_rows, len(intents)),
        },
    }

    pareto_models = []
    for model in measured:
        point = _aggregate(rows_for(model), len(intents))
        point["model"] = model
        pareto_models.append(point)

    pareto_policies = [
        {"policy": name, "quality": point["quality"], "latency_ms": point["latency_ms"]}
        for name, point in baselines.items()
    ]

    total_pairs = len(intents) * len(measured)
    cached_pairs = sum(1 for row in rows if row.answer is not None)
    return {
        "prompts": len(intents),
        "models": measured,
        "complete": cached_pairs >= total_pairs > 0,
        "missing_calls": max(total_pairs - cached_pairs, 0),
        "baselines": baselines,
        "pareto": {"models": pareto_models, "policies": pareto_policies},
        "debug_r3_choices": choices,
        "note": (
            f"Honest sample: {len(intents)} labelled prompts; these numbers describe "
            "this sample only, never the full distribution of requests."
        ),
    }


def _fastest(measured: Iterable[str], rows) -> str | None:
    """The measured model with the lowest mean latency (ties: alphabetical id)."""
    by_model: dict[str, list[float]] = {}
    for row in rows:
        if row.answer is not None and row.latency_ms is not None:
            by_model.setdefault(row.model, []).append(row.latency_ms)
    if not by_model:
        return None
    return min(sorted(by_model), key=lambda model: _mean(by_model[model]) or float("inf"))


# --- CLI ------------------------------------------------------------------------


def chat_models_from_card() -> list[str]:
    """The card's chat models (no network) for planning with an empty cache."""
    card = load_card()
    return sorted(
        model_id
        for model_id, entry in card.models.items()
        if model_id not in card.non_chat and entry.get("excluded_from_policy") is not True
    )


def _plan_report(db, intents, models: list[str]) -> dict:
    plan = bench_matrix.plan(db, intents, models)
    return {
        "prompts": len(intents),
        "models": len(models),
        "total_calls": plan.total_calls,
        "cached_calls": plan.cached_calls,
        "remaining_calls": plan.remaining_calls,
    }


def _print_table(report: dict) -> None:
    print(f"prompts: {report['prompts']}  complete: {report['complete']}  "
          f"missing calls: {report['missing_calls']}")
    print(f"note: {report['note']}")
    print()
    header = f"{'policy':<8} {'model':<24} {'quality':>8} {'latency_ms':>12} {'coverage':>9}"
    print(header)
    print("-" * len(header))
    for name in ("R0", "R1", "R2", "R3"):
        point = report["baselines"][name]
        quality = "n/a" if point["quality"] is None else f"{point['quality']:.2f}"
        latency = "n/a" if point["latency_ms"] is None else f"{point['latency_ms']:.1f}"
        model = point["model"] or "-"
        print(f"{name:<8} {model:<24} {quality:>8} {latency:>12} {point['coverage']:>8.0%}")
    print()
    print("Pareto points (per unique model):")
    for point in report["pareto"]["models"]:
        quality = "n/a" if point["quality"] is None else f"{point['quality']:.2f}"
        print(f"  {point['model']:<24} quality={quality:<6} latency_ms={point['latency_ms']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline evaluation of routing policies over the cached quality matrix."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="path to the SQLite matrix cache")
    parser.add_argument("--intents", default=str(DEFAULT_INTENTS), help="labelled prompt dataset")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the call plan (how many calls a matrix run would cost) and exit",
    )
    parser.add_argument(
        "--models", default="",
        help="comma-separated model ids for --dry-run; defaults to cache models, else the card",
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="NETWORK: run the (resumable) matrix generation; reserved for a human decision",
    )
    parser.add_argument(
        "--judge", action="store_true",
        help="NETWORK: judge cached answers that have no score yet",
    )
    parser.add_argument("--concurrency", type=int, default=4, help="in-flight call limit")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="generation cap in tokens (default 4096; raise only for hard cells)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="per-call timeout in seconds (default 240)",
    )
    parser.add_argument("--json", dest="json_path", default=None, help="write the report as JSON")
    args = parser.parse_args(argv)

    db = open_db(args.db)
    intents = bench_matrix.load_intents(args.intents)

    if args.dry_run:
        source = "--models" if args.models else "cache"
        if args.models:
            models = [model.strip() for model in args.models.split(",") if model.strip()]
        else:
            models = sorted({row.model for row in all_rows(db) if row.answer is not None})
            if not models:
                # Planning must never call the network: an empty cache plans
                # against the local card. The live catalog may differ.
                models = chat_models_from_card()
                source = "card"
        plan = _plan_report(db, intents, models)
        print(f"model source: {source}")
        print(json.dumps(plan, indent=2))
        return 0

    if args.generate:
        return asyncio.run(_generate(db, intents, args))

    if args.judge:
        return asyncio.run(_judge(db, intents, args))

    report = evaluate(db, intents)
    _print_table(report)
    print()
    print(json.dumps(report, indent=2))
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["complete"]:
        print(
            f"\nThe matrix is incomplete ({report['missing_calls']} calls missing): "
            "these baselines only cover the cached rows.",
            file=sys.stderr,
        )
    return 0


async def _generate(db, intents, args) -> int:  # pragma: no cover - network mode
    """The live matrix run: resumable, bounded, patient. Costs real quota."""
    from nan_auto.settings import Settings

    settings = Settings()
    if not settings.nan_key:
        print("No API key configured (set NAN_KEY or .env); refusing to run.", file=sys.stderr)
        return 1
    async with httpx.AsyncClient(base_url=settings.nan_base_url) as client:
        timeout = (
            args.timeout if args.timeout is not None else max(settings.upstream_timeout_s, 240.0)
        )
        call = bench_matrix.make_chat_call(
            client,
            settings.nan_key,
            timeout=timeout,
            max_tokens=args.max_tokens if args.max_tokens is not None else 4096,
        )
        if args.models:
            models = [model.strip() for model in args.models.split(",") if model.strip()]
        else:
            models = await bench_matrix.fetch_chat_models(client, settings.nan_key)
        before = bench_matrix.plan(db, intents, models)
        print(f"plan: {before.total_calls} calls, {before.cached_calls} cached, "
              f"{before.remaining_calls} to run, concurrency={args.concurrency}")
        summary = await bench_matrix.run_matrix(
            db, intents, models, call=call, concurrency=args.concurrency
        )
        print(f"done: ran={summary.ran_calls} failed={summary.failed_calls} "
              f"skipped={summary.skipped_calls}")
    return 0 if summary.failed_calls == 0 else 1


async def _judge(db, intents, args) -> int:  # pragma: no cover - network mode
    from nan_auto.settings import Settings

    settings = Settings()
    if not settings.nan_key:
        print("No API key configured (set NAN_KEY or .env); refusing to run.", file=sys.stderr)
        return 1
    async with httpx.AsyncClient(base_url=settings.nan_base_url) as client:
        call = bench_matrix.make_chat_call(
            client, settings.nan_key, timeout=max(settings.upstream_timeout_s, 240.0)
        )
        judged = await bench_judge.score_pending(db, prompts=intents, call=call)
        print(f"judged: {judged}")
    return 0
