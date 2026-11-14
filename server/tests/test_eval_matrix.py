"""NAU-14: quality matrix generator — cache, plan maths, resume, limiter, backoff.

Every test here runs offline. The chat client is exercised through `respx`,
the harness itself through injected fake calls, and the evaluation maths
through synthetic cache rows. No test in this file touches the network.
"""

# bench lives in eval/, outside the server root; pyright cannot follow it there.
# pyright: reportMissingImports=false

import asyncio
import json
import sys
from pathlib import Path

import httpx
import respx

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
sys.path.insert(0, str(EVAL_DIR))

from bench import judge as bench_judge  # noqa: E402
from bench import matrix as bench_matrix  # noqa: E402
from bench.cache import (  # noqa: E402
    get_result,
    open_db,
    rows_with_answer,
    save_result,
)
from bench.matrix import CallResult, Intent, RateLimited  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[2] / "eval" / "data"


def make_prompts(count: int, *, prefix: str = "p") -> list[Intent]:
    return [
        Intent(id=f"{prefix}{i}", prompt=f"prompt number {i}", task="factual_qa", difficulty=1)
        for i in range(count)
    ]


def sleep_recorder(into: list[float]):
    """An async injectable sleep that records the requested durations."""

    async def record(seconds: float) -> None:
        into.append(seconds)

    return record


class ProbeCall:
    """A deterministic stand-in for the chat API that records every call."""

    def __init__(self, *, latency_ms: float = 5.0) -> None:
        self.calls: list[tuple[str, str]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.latency_ms = latency_ms

    async def __call__(self, prompt: str, model: str) -> CallResult:
        self.calls.append((prompt, model))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.05)
        self.in_flight -= 1
        return CallResult(
            latency_ms=self.latency_ms, prompt_tokens=3, completion_tokens=7, answer="ok"
        )


# --- NAU-14-T1: the dataset ---------------------------------------------------


def test_intents_dataset_is_labeled_and_complete() -> None:
    lines = (DATA_DIR / "intents.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 20, "the dataset must hold at least 20 labelled prompts"
    ids: set[str] = set()
    languages: set[str] = set()
    code_fenced = 0
    for line in lines:
        item = json.loads(line)
        assert set(item) == {"id", "prompt", "task", "difficulty"}, item["id"]
        assert item["id"] not in ids, f"duplicated id {item['id']}"
        ids.add(item["id"])
        assert item["task"] in bench_matrix.TASKS, item
        assert isinstance(item["difficulty"], int) and 0 <= item["difficulty"] <= 4, item
        if "```" in item["prompt"]:
            code_fenced += 1
        # Cheap language probe: leading Spanish punctuation or ñ/á/é markers.
        if any(ch in item["prompt"] for ch in "¿¡áéíóúñ"):
            languages.add("es")
        else:
            languages.add("en")
    assert "es" in languages and "en" in languages, "dataset must mix Spanish and English"
    assert code_fenced >= 2, "dataset must include prompts with code fences"


# --- NAU-14-T2: cache and plan maths ------------------------------------------


def test_cache_roundtrip_stores_every_column() -> None:
    db = open_db(Path(":memory:"))
    save_result(
        db,
        "p1",
        "m1",
        latency_ms=120.5,
        prompt_tokens=11,
        completion_tokens=22,
        answer="hello",
    )
    row = get_result(db, "p1", "m1")
    assert row is not None
    assert row.latency_ms == 120.5
    assert row.prompt_tokens == 11
    assert row.completion_tokens == 22
    assert row.answer == "hello"
    # Quality is decided later by the judge, never by the generator.
    assert row.quality_score is None
    assert row.judge_raw is None
    assert get_result(db, "p1", "missing-model") is None


def test_plan_counts_the_calls_a_full_run_would_cost() -> None:
    db = open_db(Path(":memory:"))
    # 3 prompts x 2 models = 6 calls; two are already cached.
    save_result(db, "p0", "m1", latency_ms=1, prompt_tokens=1, completion_tokens=1, answer="a")
    save_result(db, "p0", "m2", latency_ms=1, prompt_tokens=1, completion_tokens=1, answer="a")
    plan = bench_matrix.plan(db, make_prompts(3), ["m1", "m2"])
    assert plan.total_calls == 6
    assert plan.cached_calls == 2
    assert plan.remaining_calls == 4


def test_plan_ignores_errored_rows_when_counting_cached_calls() -> None:
    db = open_db(Path(":memory:"))
    save_result(
        db,
        "p0",
        "m1",
        latency_ms=None,
        prompt_tokens=None,
        completion_tokens=None,
        answer=None,
        error="boom",
    )
    plan = bench_matrix.plan(db, make_prompts(1), ["m1"])
    assert plan.cached_calls == 0
    assert plan.remaining_calls == 1


# --- NAU-14-T2: resume behaviour ----------------------------------------------


async def test_second_run_skips_every_cached_pair() -> None:
    db = open_db(Path(":memory:"))
    probes = ProbeCall()
    prompts = make_prompts(3)
    models = ["m1", "m2"]

    await bench_matrix.run_matrix(db, prompts, models, call=probes)
    first_run = len(probes.calls)
    assert first_run == 6
    assert bench_matrix.plan(db, prompts, models).remaining_calls == 0

    await bench_matrix.run_matrix(db, prompts, models, call=probes)
    assert len(probes.calls) == first_run, "resume must not repeat cached calls"


async def test_resume_retries_pairs_that_errored_but_skips_answered_ones() -> None:
    db = open_db(Path(":memory:"))
    prompts = make_prompts(1)
    flaky_first_attempt = bench_matrix.CallError("boom")

    class OnceFailing:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, prompt: str, model: str) -> CallResult:
            self.calls += 1
            if self.calls == 1:
                raise flaky_first_attempt
            return CallResult(latency_ms=1, prompt_tokens=1, completion_tokens=1, answer="ok")

    caller = OnceFailing()
    await bench_matrix.run_matrix(db, prompts, ["m1"], call=caller, max_retries=0)
    assert get_result(db, "p0", "m1") is None or get_result(db, "p0", "m1").answer is None

    await bench_matrix.run_matrix(db, prompts, ["m1"], call=caller)
    row = get_result(db, "p0", "m1")
    assert row is not None and row.answer == "ok"
    assert caller.calls == 2, "an errored pair must be retried; an answered one never repeated"


# --- NAU-14-T3: concurrency limiter and backoff -------------------------------


async def test_run_matrix_never_exceeds_the_concurrency_limit() -> None:
    db = open_db(Path(":memory:"))
    probe = ProbeCall()
    await bench_matrix.run_matrix(db, make_prompts(8), ["m1", "m2"], call=probe, concurrency=2)
    assert probe.max_in_flight <= 2
    assert len(probe.calls) == 16


async def test_rate_limited_calls_back_off_and_are_retried() -> None:
    db = open_db(Path(":memory:"))
    sleeps: list[float] = []

    class Flaky:
        def __init__(self, failures: int) -> None:
            self.remaining = failures
            self.calls = 0

        async def __call__(self, prompt: str, model: str) -> CallResult:
            self.calls += 1
            if self.calls <= self.remaining:
                raise RateLimited("429")
            return CallResult(latency_ms=1, prompt_tokens=1, completion_tokens=1, answer="ok")

    flaky = Flaky(failures=2)
    await bench_matrix.run_matrix(
        db, make_prompts(1), ["m1"], call=flaky, backoff_s=0.5, sleep=sleep_recorder(sleeps)
    )
    assert flaky.calls == 3
    assert sleeps == [0.5, 1.0], "backoff must grow exponentially per retry"
    row = get_result(db, "p0", "m1")
    assert row is not None and row.answer == "ok"


async def test_persistent_failure_is_recorded_not_raised() -> None:
    db = open_db(Path(":memory:"))
    sleeps: list[float] = []

    class AlwaysFailing:
        async def __call__(self, prompt: str, model: str) -> CallResult:
            raise bench_matrix.CallError("transport exploded")

    await bench_matrix.run_matrix(
        db,
        make_prompts(1),
        ["m1"],
        call=AlwaysFailing(),
        max_retries=1,
        backoff_s=0.0,
        sleep=sleep_recorder(sleeps),
    )
    row = get_result(db, "p0", "m1")
    assert row is not None
    assert row.answer is None
    assert "transport exploded" in (row.error or "")
    # The failed row must not count as done work.
    assert bench_matrix.plan(db, make_prompts(1), ["m1"]).remaining_calls == 1
    assert rows_with_answer(db) == 0


# --- the real client adapter (respx; no network) ------------------------------


def _openai_payload() -> dict:
    return {
        "choices": [{"message": {"content": "Hola"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4},
    }


@respx.mock
async def test_chat_call_sends_an_openai_request_and_parses_the_answer() -> None:
    route = respx.post("http://test/chat/completions").mock(
        return_value=httpx.Response(200, json=_openai_payload())
    )
    async with httpx.AsyncClient(base_url="http://test") as client:
        call = bench_matrix.make_chat_call(client, api_key="secret-key", max_tokens=64)
        result = await call("hola", "deepseek-v4-flash")

    assert result.answer == "Hola"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 4
    assert result.latency_ms >= 0

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "deepseek-v4-flash"
    assert body["temperature"] == 0, "the benchmark must ask for deterministic answers"
    assert body["max_tokens"] == 64
    assert body["messages"] == [{"role": "user", "content": "hola"}]
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret-key"


@respx.mock
async def test_a_model_that_rejects_temperature_is_retried_without_it() -> None:
    """kimi-k3.1-pro rejects any explicit temperature (measured 2026-11-14);
    the harness must degrade to provider-default sampling, once, not give up."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "temperature" in body:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Invalid request. Check your request parameters.",
                        "type": "invalid_request_error",
                    }
                },
            )
        return httpx.Response(200, json=_openai_payload())

    route = respx.post("http://test/chat/completions").mock(side_effect=handler)
    async with httpx.AsyncClient(base_url="http://test") as client:
        call = bench_matrix.make_chat_call(client, api_key="k")
        result = await call("hi", "kimi-k3.1-pro")

    assert result.answer == "Hola"
    retry_body = json.loads(route.calls.last.request.content)
    assert "temperature" not in retry_body, "the retry must omit temperature"
    assert retry_body["model"] == "kimi-k3.1-pro"


@respx.mock
async def test_chat_call_surfaces_429_as_rate_limited() -> None:
    respx.post("http://test/chat/completions").mock(return_value=httpx.Response(429))
    async with httpx.AsyncClient(base_url="http://test") as client:
        call = bench_matrix.make_chat_call(client, api_key="k")
        try:
            await call("hi", "m1")
        except RateLimited:
            pass
        else:
            raise AssertionError("a 429 must surface as RateLimited for the harness to back off")


@respx.mock
async def test_fetch_chat_models_never_offers_hidden_or_non_chat_models() -> None:
    respx.get("http://test/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gemma4", "mode": "chat"},
                    {"id": "flux-2-klein", "mode": "chat"},  # image generator the card excludes
                    {"id": "minimax-h3", "mode": "chat"},  # excluded_from_policy
                    {"id": "unknown-model", "mode": "chat"},
                ]
            },
        )
    )
    async with httpx.AsyncClient(base_url="http://test") as client:
        ids = await bench_matrix.fetch_chat_models(client, api_key="k", url="http://test/models")
    assert ids == ["gemma4", "unknown-model"]


# --- NAU-14-T4: the judge ------------------------------------------------------


def test_parse_score_accepts_only_the_documented_scale() -> None:
    assert bench_judge.parse_score("Score: 3") == 3
    assert bench_judge.parse_score("score = 2") == 2
    assert bench_judge.parse_score("Score 4") == 4
    assert bench_judge.parse_score("the answer is 7 because 3 + 4") is None
    assert bench_judge.parse_score("Score: 9") is None  # outside 0-4
    assert bench_judge.parse_score("no score here") is None
    assert bench_judge.parse_score("") is None


def test_the_rubric_is_a_deterministic_documented_scale() -> None:
    assert bench_judge.RUBRIC["scale"] == {0: "unusable", 4: "excellent"} | {
        1: "poor",
        2: "acceptable",
        3: "good",
    }
    message = bench_judge.judge_message("what is 2+2?", "def add(): ...", task="math_or_logic")
    again = bench_judge.judge_message("what is 2+2?", "def add(): ...", task="math_or_logic")
    assert message == again, "the same inputs must always build the same judge prompt"
    assert "0" in message and "4" in message  # the scale is stated in the prompt
    assert "def add(): ..." in message  # the judged answer is included verbatim


async def test_score_pending_writes_raw_reply_and_score_and_never_repeats() -> None:
    db = open_db(Path(":memory:"))
    save_result(db, "p0", "m1", latency_ms=1, prompt_tokens=1, completion_tokens=1, answer="42")
    save_result(db, "p1", "m1", latency_ms=1, prompt_tokens=1, completion_tokens=1, answer=None)

    seen: list[tuple[str, str]] = []

    async def judge_call(prompt: str, model: str) -> CallResult:
        seen.append((prompt, model))
        return CallResult(latency_ms=1, prompt_tokens=1, completion_tokens=1, answer="Score: 3")

    await bench_judge.score_pending(
        db, prompts=make_prompts(2), judge_model="glm5.3", call=judge_call
    )
    row = get_result(db, "p0", "m1")
    assert row.quality_score == 3
    assert row.judge_raw == "Score: 3", "the raw judge reply must be kept for audit"
    assert row.judge_model == "glm5.3"
    # The errored row (no answer) is not judged.
    assert len(seen) == 1

    await bench_judge.score_pending(
        db, prompts=make_prompts(2), judge_model="glm5.3", call=judge_call
    )
    assert len(seen) == 1, "judging must be resumable too: never re-judge a scored answer"
