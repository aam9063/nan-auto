"""NAU-16a T3/T4/T5 tests: `GET /v1/stream` as Server-Sent Events.

Acceptance: a routed decision emits one event and a subscriber receives it
(T3); a slow or disconnected subscriber neither disturbs the others nor
leaves a dangling task — the publisher drops it instead of blocking (T4); and
the events carry the same typed facts as the read API, never prompt text or a
key (T5).

The SSE reading drives the app through the raw ASGI convention for the same
reason `test_proxy_stream.py` does: `httpx.ASGITransport` buffers a whole ASGI
body before returning, which would make "arrives as it is recorded" and
"disconnect ends the generator" untestable.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport

from nan_auto.clef_client.types import ChoiceAnswer, Decision, NoulAnswer, ScoreAnswer
from nan_auto.gateway.app import create_app
from nan_auto.gateway.log_store import DecisionLogStore
from nan_auto.registry.catalog import load_card
from nan_auto.registry.state import Registry
from nan_auto.settings import Settings

UPSTREAM_CHAT = "https://api.nan.builders/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
CANARY_PROMPT = "PROMPT-CANARY-nau16-stream"
CANARY_KEY = "sk-client-CANARY-KEY-nau16-stream"


class StaticProvider:
    """A `DecisionProvider` double that answers a canned decision."""

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


def make_app(store: DecisionLogStore | None = None) -> Any:  # noqa: ANN401
    return create_app(
        registry=Registry(load_card()),
        provider=StaticProvider(),
        settings=Settings(default_model=DEFAULT_MODEL),
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
        return await client.post("/v1/chat/completions", json=body, headers=headers)


async def next_decision_event(call: AsgiCall, timeout: float = 2.0) -> dict:
    """Collect SSE chunks until one carries a `decision` event; return its JSON."""
    deadline = asyncio.get_running_loop().time() + timeout
    buffer = b""
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            pytest.fail("no decision event arrived in time")
        chunk = await asyncio.wait_for(call.bodies.get(), remaining)
        if chunk is None:
            pytest.fail("the SSE stream ended before a decision event arrived")
        buffer += chunk
        if b"event: decision" not in buffer:
            continue
        data = buffer.split(b"data: ", 1)[1]
        return json.loads(data.split(b"\n", 1)[0].decode("utf-8"))


class AsgiCall:
    """Collects the status, headers and body chunks of one raw ASGI request."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.status_seen = asyncio.Event()
        self.body_done = asyncio.Event()
        self.bodies: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def first_body(self, timeout: float = 2.0) -> bytes | None:
        return await asyncio.wait_for(self.bodies.get(), timeout)


async def run_asgi_stream(app: Any, call: AsgiCall) -> None:  # noqa: ANN401
    """Send one raw ASGI GET /v1/stream and collect response messages."""
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/v1/stream",
        "raw_path": b"/v1/stream",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
    }
    request_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if request_sent:
            # The client keeps the connection open until body_done is set;
            # then it goes away, as a disconnected SSE client would.
            await call.body_done.wait()
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
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

    try:
        await app(scope, receive, send)
    finally:
        call.body_done.set()
        await call.bodies.put(None)


# --- T3: a routed decision is streamed to the subscriber -----------------------


@respx.mock
async def test_a_routed_decision_arrives_as_one_sse_event(tmp_path: Any) -> None:  # noqa: ANN401
    store = DecisionLogStore(tmp_path / "decisions.db")
    await store.open()
    app = make_app(store=store)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))

    call = AsgiCall()
    task = asyncio.create_task(run_asgi_stream(app, call))
    await asyncio.wait_for(call.status_seen.wait(), 2.0)
    assert call.status == 200
    assert call.headers["content-type"].startswith("text/event-stream")

    routed = await post_json(app, chat_body())
    assert routed.status_code == 200

    event = await next_decision_event(call)
    assert event["chosen_model"] == routed.headers["x-nan-auto-model"]
    assert event["reasoning_level"] == "off"
    assert event["decision_source"] == "clef"
    assert [answer["question_id"] for answer in event["answers"]] == [
        "task",
        "difficulty",
        "needs_reasoning",
        "expected_output_length",
    ]
    assert len(event["attempts"]) == 1
    assert event["attempts"][0]["outcome"] == "success"

    # Disconnect: the stream ends and nothing is left hanging.
    call.body_done.set()
    await asyncio.wait_for(task, 2.0)


@respx.mock
async def test_without_a_store_no_events_are_published(tmp_path: Any) -> None:  # noqa: ANN401
    app = make_app(store=None)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))
    call = AsgiCall()
    task = asyncio.create_task(run_asgi_stream(app, call))
    await asyncio.wait_for(call.status_seen.wait(), 2.0)
    await post_json(app, chat_body())
    for _ in range(20):
        await asyncio.sleep(0)
    routed = await post_json(app, chat_body())
    assert routed.status_code == 200
    call.body_done.set()
    await asyncio.wait_for(task, 2.0)
    # The connect comment is the only thing the subscriber ever saw.
    assert call.bodies.qsize() <= 2


# --- T4: a slow or disconnected subscriber disturbs no one ---------------------


@respx.mock
async def test_a_disconnected_subscriber_does_not_disturb_the_others(
    tmp_path: Any,  # noqa: ANN401
) -> None:
    store = DecisionLogStore(tmp_path / "decisions.db")
    await store.open()
    app = make_app(store=store)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))

    first, second = AsgiCall(), AsgiCall()
    task_first = asyncio.create_task(run_asgi_stream(app, first))
    task_second = asyncio.create_task(run_asgi_stream(app, second))
    await asyncio.wait_for(first.status_seen.wait(), 2.0)
    await asyncio.wait_for(second.status_seen.wait(), 2.0)

    routed = await post_json(app, chat_body())
    assert routed.status_code == 200
    event_one = await next_decision_event(first)
    event_two = await next_decision_event(second)
    assert event_one["id"] == event_two["id"]

    # The first subscriber goes away: its stream must end and leave no task.
    first.body_done.set()
    await asyncio.wait_for(task_first, 2.0)
    assert task_first.done()

    routed = await post_json(app, chat_body(stream=False))
    assert routed.status_code == 200
    event_two_again = await next_decision_event(second)
    assert event_two_again["id"] != event_two["id"]  # the survivor still receives
    second.body_done.set()
    await asyncio.wait_for(task_second, 2.0)


@respx.mock
async def test_a_slow_subscriber_is_dropped_and_the_publisher_keeps_going(
    tmp_path: Any,  # noqa: ANN401
) -> None:
    store = DecisionLogStore(tmp_path / "decisions.db")
    await store.open()
    app = make_app(store=store)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))

    call = AsgiCall()
    task = asyncio.create_task(run_asgi_stream(app, call))
    await asyncio.wait_for(call.status_seen.wait(), 2.0)

    bus = app.state.decision_bus
    # Flood past the subscriber's queue bound: the publisher must not block and
    # the slow subscriber must be dropped (its stream ends with the sentinel).
    for index in range(200):
        bus.publish({"sequence": index})

    # The publisher stays usable: a fresh subscriber still receives events.
    fresh = bus.subscribe()
    bus.publish({"sequence": "after"})
    assert fresh.queue.qsize() == 1
    bus.unsubscribe(fresh)

    # The flooded SSE consumer ends on its own — no dangling generator.
    await asyncio.wait_for(task, 2.0)
    assert task.done()

    # And routing still works after the flood: a real decision still lands.
    routed = await post_json(app, chat_body())
    assert routed.status_code == 200
    again = bus.subscribe()
    bus.publish({"sequence": "still-alive"})
    assert again.queue.qsize() == 1
    bus.unsubscribe(again)


# --- T5: the stream event carries no prompt text and no key --------------------


@respx.mock
async def test_the_stream_event_carries_no_prompt_or_key(tmp_path: Any) -> None:  # noqa: ANN401
    store = DecisionLogStore(tmp_path / "decisions.db")
    await store.open()
    app = make_app(store=store)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))

    call = AsgiCall()
    task = asyncio.create_task(run_asgi_stream(app, call))
    await asyncio.wait_for(call.status_seen.wait(), 2.0)

    routed = await post_json(
        app,
        chat_body(messages=[{"role": "user", "content": CANARY_PROMPT}]),
        headers={"Authorization": CANARY_KEY},
    )
    assert routed.status_code == 200
    event = await next_decision_event(call)
    text = json.dumps(event)
    assert CANARY_PROMPT not in text
    assert CANARY_KEY not in text

    call.body_done.set()
    await asyncio.wait_for(task, 2.0)
