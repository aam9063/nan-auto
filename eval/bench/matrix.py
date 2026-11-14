"""The quality-matrix generator (NAU-14, spec 11.1 and 11.6).

For every prompt and every chat model of the catalog the harness asks for one
answer, measures the call and caches it in SQLite keyed by (prompt_id, model).
A second run skips everything the cache already holds, so relaunching after an
interruption never repeats work or spends quota twice.

The only network-capable pieces here are `make_chat_call` (the OpenAI-shaped
client) and `fetch_chat_models` (the live catalog). Everything else — the plan
maths, the limiter, the retry walk — is pure and testable without a network.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx  # noqa: E402

from bench.cache import answered_pairs, save_result
from nan_auto.gateway.fallback import ConcurrencyLimiter  # noqa: E402
from nan_auto.registry.catalog import DEFAULT_URL, CatalogClient, load_card, merge
from nan_auto.registry.types import ModelCard

ChatCall = Callable[[str, str], Awaitable["CallResult"]]
Sleep = Callable[[float], Awaitable[None]]

# The ten labels of the Clef questionnaire (spec 6.3); the dataset uses them.
TASKS: tuple[str, ...] = (
    "chat_casual",
    "factual_qa",
    "coding_generate",
    "coding_debug_or_review",
    "math_or_logic",
    "summarize_or_extract",
    "translate_or_rewrite",
    "creative_writing",
    "agentic_planning",
    "other",
)


class RateLimited(Exception):
    """The upstream answered 429: the harness must back off and retry."""


class CallError(Exception):
    """One call failed in a way that is not a 429 (transport, 4xx, bad body)."""


@dataclass(frozen=True)
class CallResult:
    """What one successful model call produced."""

    latency_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    answer: str | None


@dataclass(frozen=True)
class Intent:
    """One labelled prompt of the dataset."""

    id: str
    prompt: str
    task: str
    difficulty: int


@dataclass(frozen=True)
class Plan:
    """How many calls a full matrix run would still cost."""

    total_calls: int
    cached_calls: int
    remaining_calls: int


@dataclass(frozen=True)
class RunSummary:
    """What one generator pass did."""

    ran_calls: int
    failed_calls: int
    skipped_calls: int


def load_intents(path: Path | str) -> list[Intent]:
    """Parse `intents.jsonl`; a malformed line is an error naming the line."""
    intents: list[Intent] = []
    seen: set[str] = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        try:
            intent = Intent(
                id=item["id"],
                prompt=item["prompt"],
                task=item["task"],
                difficulty=int(item["difficulty"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}:{number}: invalid intent ({exc})") from exc
        if intent.task not in TASKS:
            raise ValueError(f"{path}:{number}: unknown task {intent.task!r}")
        if not 0 <= intent.difficulty <= 4:
            raise ValueError(f"{path}:{number}: difficulty {intent.difficulty} outside 0-4")
        if intent.id in seen:
            raise ValueError(f"{path}:{number}: duplicated id {intent.id!r}")
        seen.add(intent.id)
        intents.append(intent)
    return intents


# --- plan maths (pure, no network) --------------------------------------------


def plan(db, prompts: Sequence[Intent], models: Iterable[str]) -> Plan:
    """Calls a full run still needs: every (prompt, model) minus cached ones.

    Only rows with a real answer count as cached; errored rows are pending
    work, not done work.
    """
    pairs = {(intent.id, model) for intent in prompts for model in models}
    cached = len(pairs & answered_pairs(db))
    return Plan(total_calls=len(pairs), cached_calls=cached, remaining_calls=len(pairs) - cached)


# --- the real client adapter ---------------------------------------------------


def make_chat_call(
    client: httpx.AsyncClient,
    api_key: str,
    *,
    timeout: float = 240.0,
    max_tokens: int = 4096,
) -> ChatCall:
    """Build the OpenAI-shaped `/chat/completions` call used for the matrix.

    Determinism first: `temperature=0`. A 429 surfaces as `RateLimited` so the
    harness (not the transport layer) owns the backoff policy.

    `max_tokens` is a uniform cap for every model, sized so reasoning-heavy
    models still emit an answer after their thinking: measured 2026-11-14,
    qwen3.6 with the default reasoning spends 1024 tokens thinking alone and
    returns `content: None`. The same cap for everyone keeps the comparison
    fair; rows regenerated with a different cap would not be comparable.
    """

    async def call(prompt: str, model: str) -> CallResult:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        started = time.perf_counter()
        try:
            response = await client.post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
            if response.status_code >= 400 and "temperature" in body:
                # Measured 2026-11-14: kimi-k3.1-pro rejects ANY explicit
                # temperature (even 0.01) with a parameterless 400. Retry once
                # without it: the row then samples at provider default, an
                # honest, documented loss of determinism for that model only.
                retry_body = {k: v for k, v in body.items() if k != "temperature"}
                response = await client.post(
                    "/chat/completions",
                    json=retry_body,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=timeout,
                )
        except httpx.TimeoutException as exc:
            raise CallError(f"chat timeout after {timeout}s") from exc
        except httpx.HTTPError as exc:
            raise CallError(f"chat transport error: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        if response.status_code == 429:
            raise RateLimited("chat/completions answered 429")
        if response.status_code >= 400:
            # Strip the key from server-controlled text before it reaches a message.
            text = response.text.replace(api_key, "[redacted]") if api_key else response.text
            raise CallError(f"chat returned {response.status_code}: {text[:200]}")
        try:
            payload = response.json()
            answer = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {})
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise CallError(f"chat returned an unreadable body: {exc}") from exc
        if not isinstance(answer, str):
            raise CallError("chat returned no message content")
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        return CallResult(
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
            answer=answer,
        )

    return call


async def fetch_chat_models(
    client: httpx.AsyncClient,
    api_key: str,
    *,
    url: str = DEFAULT_URL,
    card: ModelCard | None = None,
) -> list[str]:
    """The chat model ids of the live catalog, merged with the local card.

    The catalog drives the list (the card only adds metadata); image
    generators the card hides and models excluded from the policy
    (`excluded_from_policy`) never enter the matrix.
    """
    entries = await CatalogClient(client, api_key, url=url).fetch()
    merged = merge(entries, card if card is not None else load_card())
    return sorted(
        entry.id
        for entry in entries
        if entry.id in merged and not merged[entry.id].excluded
    )


# --- generator ----------------------------------------------------------------


async def call_with_backoff(
    call: ChatCall,
    prompt: str,
    model: str,
    *,
    max_retries: int = 4,
    backoff_s: float = 0.5,
    sleep: Sleep = asyncio.sleep,
) -> CallResult:
    """One call, retried on 429 with an exponential backoff (0.5s, 1s, 2s...).

    `sleep` is injectable so tests can observe the walk without waiting.
    Non-429 failures (`CallError`) are not retried here: the caller decides.
    """
    if max_retries < 0:
        raise ValueError("max_retries cannot be negative")
    for attempt in range(max_retries + 1):
        try:
            return await call(prompt, model)
        except RateLimited:
            if attempt == max_retries:
                raise
            await sleep(backoff_s * (2**attempt))
    raise AssertionError("unreachable: the retry loop always returns or raises")


async def run_matrix(
    db,
    prompts: Sequence[Intent],
    models: Iterable[str],
    *,
    call: ChatCall,
    concurrency: int = 4,
    max_retries: int = 4,
    backoff_s: float = 0.5,
    sleep: Sleep = asyncio.sleep,
) -> RunSummary:
    """Run every missing (prompt, model) pair once, cached, bounded, patient.

    `concurrency` bounds in-flight calls against the shared key; a 429 arrives
    as `RateLimited` and is retried with an exponential backoff (`backoff_s`,
    doubled per attempt). Any other failure ends that pair and is recorded as
    an errored row, which a later run retries instead of counting as done.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if max_retries < 0:
        raise ValueError("max_retries cannot be negative")
    limiter = ConcurrencyLimiter(limit=concurrency)
    done = answered_pairs(db)
    model_list = list(models)
    work = [
        (intent, model)
        for intent in prompts
        for model in model_list
        if (intent.id, model) not in done
    ]
    skipped = len(prompts) * len(model_list) - len(work)

    async def one(intent: Intent, model: str) -> tuple[str, str, str | None]:
        """Answer one pair; return (prompt_id, model, error-or-None)."""
        try:
            async with limiter.slot("benchmark"):
                result = await call_with_backoff(
                    call,
                    intent.prompt,
                    model,
                    max_retries=max_retries,
                    backoff_s=backoff_s,
                    sleep=sleep,
                )
        except RateLimited as exc:
            return intent.id, model, f"429 sustained: {exc}"
        except CallError as exc:
            return intent.id, model, str(exc)
        save_result(
            db,
            intent.id,
            model,
            latency_ms=result.latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            answer=result.answer,
        )
        return intent.id, model, None

    outcomes = await asyncio.gather(*(one(intent, model) for intent, model in work))
    ran = failed = 0
    for prompt_id, model, error in outcomes:
        if error is None:
            ran += 1
        else:
            failed += 1
            save_result(
                db,
                prompt_id,
                model,
                latency_ms=None,
                prompt_tokens=None,
                completion_tokens=None,
                answer=None,
                error=error,
            )
    return RunSummary(ran_calls=ran, failed_calls=failed, skipped_calls=skipped)
