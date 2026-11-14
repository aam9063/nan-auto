"""NAU-10 integration tests: the routed fallback walk over the proxy route.

Every rule of spec 5.5 is observed through the public `/v1/chat/completions`
route with a canned Clef decision and an upstream double that answers per
requested model id: the model changes only before the first byte (a mid-stream
break is never retried — pinned by `test_proxy_stream.py`), the retry budget is
two (spec 5.3), `Retry-After` delays the next attempt, a 402 never retries the
same model, exhausting the chain returns the last real status, each attempt
reaches the decision log, and `x-nan-auto-fallback` lists the models tried
before the final one in policy order. The limiter holds one slot per client key
for the whole walk and releases it even when the client cancels.

The candidate chain is computed in-test from the same registry, features and
policy the route uses, so the tests pin the rules, not the current card.
"""

import asyncio
import contextlib
import json
import sqlite3
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
from httpx import ASGITransport

from nan_auto.clef_client.types import ChoiceAnswer, Decision, NoulAnswer, ScoreAnswer
from nan_auto.gateway.app import create_app
from nan_auto.gateway.log_store import DecisionLogStore
from nan_auto.registry.catalog import load_card
from nan_auto.registry.state import Registry
from nan_auto.router_core.features import extract_features
from nan_auto.router_core.policy import decide
from nan_auto.settings import Settings

UPSTREAM_CHAT = "https://api.nan.builders/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
CLIENT_KEY = "Bearer sk-client-canary-0"
FIRST_CHUNK = b'data: {"delta": "hel"}\n\n'
LAST_CHUNK = b"data: [DONE]\n\n"


class StaticProvider:
    """A `DecisionProvider` double that answers a canned chat_casual decision."""

    async def decide(self, *, state: str, questions: dict) -> Decision:
        return Decision(
            decision_id="decision-canned",
            model="chat",
            task=ChoiceAnswer(
                label="chat_casual", probabilities={"chat_casual": 1.0}, confidence=1.0
            ),
            difficulty=ScoreAnswer(
                score=0.5, legend={"0": "Trivial"}, probabilities={"0": 1.0}, confidence=1.0
            ),
            needs_reasoning=NoulAnswer(yes=0.0, confidence=1.0),
            expected_output_length=ScoreAnswer(
                score=0.5, legend={"0": "Short"}, probabilities={"0": 1.0}, confidence=1.0
            ),
        )


class RoutedUpstream(httpx.AsyncBaseTransport):
    """Upstream double that answers per requested model id.

    `behavior` maps a model id to a response factory; missing models fall back
    to `default`. An optional `gate` parks every answer until it is set, so a
    test can observe a request stopped inside the upstream call.
    """

    def __init__(
        self,
        behavior: Mapping[str, Callable[[], httpx.Response]] | None = None,
        default: Callable[[], httpx.Response] | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._behavior = dict(behavior or {})
        self._default = default or (lambda: httpx.Response(200, json={"id": "up-ok"}))
        self._gate = gate
        self.calls: list[httpx.Request] = []
        self.times: list[float] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.calls.append(request)
        self.times.append(time.monotonic())
        if self._gate is not None:
            await self._gate.wait()
        model = json.loads(request.content)["model"]
        return self._behavior.get(model, self._default)()


async def policy_chain() -> tuple[str, ...]:
    """The ranked candidate chain the route walks, from the same inputs it uses."""
    registry = Registry(load_card())
    await registry.refresh()
    decision = await StaticProvider().decide(state="", questions={})
    features = extract_features(
        {"model": "nan-auto", "messages": [{"role": "user", "content": "hello there"}]},
        conversation_id=None,
    )
    result = decide(
        decision,
        features,
        registry.models,
        profile="nan-auto",
        default_model=DEFAULT_MODEL,
        health={model_id: registry.health(model_id) for model_id in registry.models},
    )
    return result.candidates


def make_app(
    upstream: RoutedUpstream,
    settings: Settings | None = None,
    store: DecisionLogStore | None = None,
) -> Any:  # noqa: ANN401
    return create_app(
        registry=Registry(load_card()),
        provider=StaticProvider(),
        settings=settings or Settings(default_model=DEFAULT_MODEL),
        client=httpx.AsyncClient(transport=upstream),
        store=store,
    )


def chat_body(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    body: dict[str, Any] = {
        "model": "nan-auto",
        "messages": [{"role": "user", "content": "hello there"}],
    }
    body.update(extra)
    return body


async def post_json(
    app: Any, body: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:  # noqa: ANN401
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/v1/chat/completions",
            json=body,
            headers={"Authorization": CLIENT_KEY, **(headers or {})},
        )


def error(message: str) -> httpx.Response:
    return httpx.Response(503, json={"error": {"message": message, "type": "server_error"}})


def sse_response(gate: asyncio.Event | None = None) -> httpx.Response:
    async def stream() -> AsyncIterator[bytes]:
        yield FIRST_CHUNK
        if gate is not None:
            await gate.wait()
        yield LAST_CHUNK

    return httpx.Response(200, content=stream(), headers={"content-type": "text/event-stream"})


def call_models(upstream: RoutedUpstream) -> list[str]:
    return [json.loads(call.content)["model"] for call in upstream.calls]


def _rows(db_path: Path, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql))
    finally:
        conn.close()


async def _wait_for_rows(db_path: Path, count: int, timeout: float = 2.0) -> list[sqlite3.Row]:
    deadline = time.monotonic() + timeout
    rows = _rows(db_path, "SELECT * FROM ATTEMPT")
    while len(rows) < count and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        rows = _rows(db_path, "SELECT * FROM ATTEMPT")
    return rows


async def _wait_for_calls(upstream: RoutedUpstream, count: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while len(upstream.calls) < count and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert len(upstream.calls) == count


# --- the raw ASGI harness for streaming assertions ----------------------------


class AsgiCall:
    """Collects the status, headers, body chunks and errors of one ASGI request."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.errors: list[BaseException] = []
        self.status_seen = asyncio.Event()
        self.body_done = asyncio.Event()
        self.bodies: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def first_body(self, timeout: float = 2.0) -> bytes | None:
        return await asyncio.wait_for(self.bodies.get(), timeout)

    async def drain_body(self, timeout: float = 2.0) -> bytes:
        parts: list[bytes] = []
        while True:
            part = await asyncio.wait_for(self.bodies.get(), timeout)
            if part is None:
                return b"".join(parts)
            parts.append(part)


async def start_call(
    app: Any, body: dict[str, Any], *, wait_status: bool = True
) -> tuple[AsgiCall, asyncio.Task[None]]:  # noqa: ANN401
    """Start one streaming ASGI request as a task and wait for its status."""
    call = AsgiCall()
    raw = json.dumps(body).encode()
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"authorization", CLIENT_KEY.encode()),
        ],
    }
    request_sent = False

    async def receive() -> dict[str, Any]:  # noqa: ANN401
        nonlocal request_sent
        if request_sent:
            await call.body_done.wait()
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: dict[str, Any]) -> None:  # noqa: ANN401
        if message["type"] == "http.response.start":
            call.status = message["status"]
            call.headers = {k.decode(): v.decode() for k, v in message["headers"]}
            call.status_seen.set()
        elif message["type"] == "http.response.body":
            part = message.get("body", b"")
            if part:
                await call.bodies.put(part)
            if not message.get("more_body", False):
                call.body_done.set()
                await call.bodies.put(None)

    async def runner() -> None:
        try:
            await app(scope, receive, send)
        except BaseException as error:  # noqa: BLE001 - the relay must propagate raw
            call.errors.append(error)
        finally:
            call.body_done.set()
            await call.bodies.put(None)

    task = asyncio.create_task(runner())
    if wait_status:
        await asyncio.wait_for(call.status_seen.wait(), 2.0)
    return call, task


# --- fallback before the first byte -------------------------------------------


async def test_a_5xx_before_the_first_byte_switches_to_the_next_candidate() -> None:
    chain = await policy_chain()
    upstream = RoutedUpstream(
        behavior={
            chain[0]: lambda: error("overloaded"),
            chain[1]: lambda: httpx.Response(200, json={"id": "up-second"}),
        }
    )
    app = make_app(upstream)
    response = await post_json(app, chat_body())
    assert response.status_code == 200
    assert response.json()["id"] == "up-second"
    assert call_models(upstream) == [chain[0], chain[1]]  # policy order, no repeats
    assert response.headers["x-nan-auto-model"] == chain[1]
    assert response.headers["x-nan-auto-fallback"] == chain[0]


async def test_exhausting_the_chain_returns_the_last_real_status() -> None:
    chain = await policy_chain()
    upstream = RoutedUpstream(default=lambda: error("overloaded"))
    app = make_app(upstream)  # default settings: initial attempt + 2 retries
    response = await post_json(app, chat_body())
    assert response.status_code == 503  # the upstream status, never a generic 500
    assert response.json()["error"]["message"] == "overloaded"
    expected = list(chain[:3])
    assert call_models(upstream) == expected
    assert response.headers["x-nan-auto-model"] == expected[-1]
    assert response.headers["x-nan-auto-fallback"] == ",".join(expected[:-1])


async def test_a_client_4xx_propagates_without_fallback() -> None:
    chain = await policy_chain()

    def bad_request() -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "bad request", "type": "invalid_request_error"}}
        )

    upstream = RoutedUpstream(
        behavior={chain[0]: bad_request},
        default=lambda: httpx.Response(200, json={"id": "up-ok"}),
    )
    app = make_app(upstream)
    response = await post_json(app, chat_body())
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "bad request"
    assert len(upstream.calls) == 1  # no candidate can fix a client error
    assert response.headers["x-nan-auto-fallback"] == ""


async def test_retry_after_429_delays_the_next_attempt() -> None:
    chain = await policy_chain()

    def rate_limited() -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "slow down", "type": "rate_limit_error"}},
            headers={"Retry-After": "0.3"},
        )

    upstream = RoutedUpstream(
        behavior={chain[0]: rate_limited},
        default=lambda: httpx.Response(200, json={"id": "up-ok"}),
    )
    app = make_app(upstream)
    response = await post_json(app, chat_body())
    assert response.status_code == 200
    assert call_models(upstream) == [chain[0], chain[1]]
    assert upstream.times[1] - upstream.times[0] >= 0.3  # the wait was respected


async def test_a_429_without_retry_after_moves_on_immediately() -> None:
    chain = await policy_chain()

    def rate_limited() -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    upstream = RoutedUpstream(
        behavior={chain[0]: rate_limited},
        default=lambda: httpx.Response(200, json={"id": "up-ok"}),
    )
    app = make_app(upstream)
    response = await post_json(app, chat_body())
    assert response.status_code == 200
    assert call_models(upstream) == [chain[0], chain[1]]
    assert upstream.times[1] - upstream.times[0] < 0.3  # nothing to wait for


async def test_a_402_is_never_retried_against_the_same_model() -> None:
    chain = await policy_chain()

    def quota_exhausted() -> httpx.Response:
        return httpx.Response(402, json={"error": {"message": "quota exhausted"}})

    upstream = RoutedUpstream(
        behavior={chain[0]: quota_exhausted},
        default=lambda: httpx.Response(200, json={"id": "up-ok"}),
    )
    app = make_app(upstream)
    response = await post_json(app, chat_body())
    assert response.status_code == 200
    models = call_models(upstream)
    assert models[0] == chain[0]
    assert models.count(chain[0]) == 1  # the 402 model was not tried again
    assert response.headers["x-nan-auto-fallback"] == chain[0]


async def test_streaming_fallback_before_the_first_byte_switches_and_streams() -> None:
    chain = await policy_chain()
    upstream = RoutedUpstream(
        behavior={chain[0]: lambda: error("overloaded")},
        default=lambda: sse_response(),
    )
    app = make_app(upstream)
    call, task = await start_call(app, chat_body(stream=True))
    assert call.status == 200
    assert call.headers.get("content-type", "").startswith("text/event-stream")
    first = await call.first_body()
    assert first == FIRST_CHUNK  # bytes streamed incrementally after the switch
    await call.drain_body()
    await asyncio.wait_for(task, 2.0)
    assert call.errors == []
    assert call_models(upstream) == [chain[0], chain[1]]  # the switch happened pre-byte
    assert call.headers["x-nan-auto-model"] == chain[1]
    assert call.headers["x-nan-auto-fallback"] == chain[0]


# --- the breaker is wired into the walk ----------------------------------------


async def test_the_breaker_only_opens_the_models_that_actually_failed() -> None:
    """A model the retry budget never reached did not fail.

    Marking untried candidates would blame a healthy model for another one's
    outage and quietly degrade routing for the whole cooldown, so each walk
    opens only what it tried and the next walk discovers the next candidates.
    """
    chain = await policy_chain()
    assert len(chain) == 10  # the walk below relies on the chain being longer than the budget
    upstream = RoutedUpstream(default=lambda: error("overloaded"))
    app = make_app(upstream, Settings(default_model=DEFAULT_MODEL, breaker_threshold=1))

    first = await post_json(app, chat_body())
    assert first.status_code == 503
    assert len(upstream.calls) == 3  # one attempt plus two retries
    tried_first = [json.loads(call.content)["model"] for call in upstream.calls]
    assert tried_first == list(chain[:3])

    second = await post_json(app, chat_body())
    assert second.status_code == 503
    assert len(upstream.calls) == 6  # the next three: the first three stayed open
    tried_second = [json.loads(call.content)["model"] for call in upstream.calls[3:]]
    assert tried_second == list(chain[3:6])
    assert not set(tried_first) & set(tried_second)

    for _ in range(3):
        await post_json(app, chat_body())
    exhausted = await post_json(app, chat_body())
    # Every candidate is open now, so no upstream call happens and there is no
    # real status to propagate.
    assert exhausted.status_code == 502
    assert exhausted.json()["error"]["code"] == "no_model"
    assert len(upstream.calls) == 10  # each candidate tried exactly once


# --- attempts reach the decision log -------------------------------------------


async def test_every_attempt_is_recorded_with_its_outcome_and_status(tmp_path: Path) -> None:
    chain = await policy_chain()
    upstream = RoutedUpstream(
        behavior={chain[0]: lambda: error("overloaded")},
        default=lambda: httpx.Response(200, json={"id": "up-second"}),
    )
    db = tmp_path / "decisions.db"
    app = make_app(upstream, store=DecisionLogStore(db))
    response = await post_json(app, chat_body())
    assert response.status_code == 200
    rows = await _wait_for_rows(db, 2)
    assert [(row["model"], row["outcome"], row["http_status"]) for row in rows] == [
        (chain[0], "http_error", 503),
        (chain[1], "success", 200),
    ]
    decisions = _rows(db, "SELECT chosen_model FROM REQUEST_LOG")
    assert decisions[0]["chosen_model"] == chain[1]


# --- the per-key concurrency limiter -------------------------------------------


async def test_the_limiter_serializes_requests_sharing_a_key() -> None:
    gate = asyncio.Event()
    upstream = RoutedUpstream(default=lambda: sse_response(gate))
    app = make_app(upstream, Settings(default_model=DEFAULT_MODEL, key_concurrency_limit=1))
    first, first_task = await start_call(app, chat_body(stream=True), wait_status=False)
    await _wait_for_calls(upstream, 1)
    second, second_task = await start_call(app, chat_body(stream=True), wait_status=False)
    await asyncio.sleep(0.05)
    assert len(upstream.calls) == 1  # the second request is parked on the slot
    assert second.status is None

    gate.set()
    first_body = await first.drain_body()
    await asyncio.wait_for(first_task, 2.0)
    assert first.status == 200 and FIRST_CHUNK in first_body
    await second.drain_body()
    await asyncio.wait_for(second_task, 2.0)
    assert second.status == 200
    assert len(upstream.calls) == 2  # it went through once the slot freed


async def test_client_cancellation_releases_the_limiter_slot() -> None:
    gate = asyncio.Event()
    upstream = RoutedUpstream(default=lambda: sse_response(gate))
    app = make_app(upstream, Settings(default_model=DEFAULT_MODEL, key_concurrency_limit=1))
    cancelled_call, cancelled_task = await start_call(
        app, chat_body(stream=True), wait_status=False
    )
    await _wait_for_calls(upstream, 1)  # the walk holds the slot, parked upstream
    cancelled_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(cancelled_task, 2.0)
    gate.set()

    # The cancelled request released the slot: this must complete, not deadlock.
    async with asyncio.timeout(2.0):
        response = await post_json(app, chat_body())
    assert response.status_code == 200
