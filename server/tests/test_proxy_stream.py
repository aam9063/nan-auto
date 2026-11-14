"""NAU-08 streaming tests: incremental SSE relay, error propagation, no retry.

The incremental assertions drive the FastAPI app through the raw ASGI calling
convention: `httpx.ASGITransport` buffers a whole ASGI body before returning,
which would make "not buffered" untestable. The ScriptedUpstream transport
feeds a scripted httpx.Response to the gateway's outbound client per call, so
streaming generators reach the route as real upstream streams would.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from httpx import ASGITransport

from nan_auto.clef_client.types import ChoiceAnswer, Decision, NoulAnswer, ScoreAnswer
from nan_auto.gateway.app import create_app
from nan_auto.registry.catalog import load_card
from nan_auto.registry.state import Registry
from nan_auto.settings import Settings

UPSTREAM_CHAT = "https://api.nan.builders/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
CLIENT_KEY = "Bearer sk-client-canary-0"

FIRST_CHUNK = b'data: {"delta": "hel"}\n\n'
LAST_CHUNK = b"data: [DONE]\n\n"


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


class ScriptedUpstream(httpx.AsyncBaseTransport):
    """Upstream double: answers every chat call with a fresh scripted response."""

    def __init__(self, factory: Callable[[], httpx.Response]) -> None:
        self._factory = factory
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.calls.append(request)
        return self._factory()


class AsgiCall:
    """Collects the response hats and body chunks of one raw ASGI request."""

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


def make_app(client: httpx.AsyncClient | None = None) -> Any:  # noqa: ANN401
    registry = Registry(load_card())
    return create_app(
        registry=registry,
        provider=StaticProvider(),
        settings=Settings(default_model=DEFAULT_MODEL),
        client=client,
    )


def chat_body(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "nan-auto",
        "messages": [{"role": "user", "content": "hello there"}],
        "stream": True,
    }
    body.update(extra)
    return body


async def start_call(
    app: Any,
    body: dict[str, Any],  # noqa: ANN401
) -> tuple[AsgiCall, asyncio.Task[None]]:
    """Start one ASGI request as a task; returns the collector and the runner."""
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

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if request_sent:
            # Keep the response open until the app finishes streaming; an
            # instant disconnect would cancel the body relay mid-stream.
            await call.body_done.wait()
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

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

    async def runner() -> None:
        try:
            await app(scope, receive, send)
        except BaseException as error:  # noqa: BLE001 - the relay must propagate raw
            call.errors.append(error)
        finally:
            call.body_done.set()
            await call.bodies.put(None)

    task = asyncio.create_task(runner())
    await asyncio.wait_for(call.status_seen.wait(), 2.0)
    return call, task


async def sse_stream(gate: asyncio.Event | None = None) -> AsyncIterator[bytes]:
    yield FIRST_CHUNK
    if gate is not None:
        await gate.wait()
    yield LAST_CHUNK


async def test_sse_is_relayed_incrementally_not_buffered() -> None:
    """The first upstream chunk reaches the client while upstream is mid-stream.

    A buffering relay would deadlock: the upstream generator waits on the gate
    before its second chunk, so the bounded first-chunk read below would time
    out instead of seeing the first byte.
    """
    gate = asyncio.Event()
    upstream = ScriptedUpstream(
        lambda: httpx.Response(
            200, content=sse_stream(gate), headers={"content-type": "text/event-stream"}
        )
    )
    app = make_app(httpx.AsyncClient(transport=upstream))
    call, task = await start_call(app, chat_body())
    assert call.status == 200
    assert call.headers.get("content-type", "").startswith("text/event-stream")

    first = await call.first_body()
    assert first == FIRST_CHUNK
    assert not gate.is_set()  # upstream was never fully consumed first

    gate.set()
    rest = await call.drain_body()
    await asyncio.wait_for(task, 2.0)
    assert call.errors == []
    assert rest == LAST_CHUNK
    assert len(upstream.calls) == 1


async def test_streaming_response_sets_decision_headers() -> None:
    upstream = ScriptedUpstream(
        lambda: httpx.Response(
            200, content=sse_stream(), headers={"content-type": "text/event-stream"}
        )
    )
    app = make_app(httpx.AsyncClient(transport=upstream))
    call, task = await start_call(app, chat_body())
    assert call.status == 200
    body = await call.drain_body()
    await asyncio.wait_for(task, 2.0)
    assert call.errors == []
    assert body == FIRST_CHUNK + LAST_CHUNK
    assert call.headers["x-nan-auto-model"]
    assert call.headers["x-nan-auto-decision-id"] == "decision-canned"
    assert call.headers["x-nan-auto-reason"]
    assert call.headers["x-nan-auto-fallback"] == ""


async def test_upstream_error_before_first_byte_propagates_with_status() -> None:
    upstream = ScriptedUpstream(
        lambda: httpx.Response(
            503, json={"error": {"message": "overloaded", "type": "server_error"}}
        )
    )
    app = make_app(httpx.AsyncClient(transport=upstream))
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/completions", json=chat_body(), headers={"Authorization": CLIENT_KEY}
        )
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "overloaded"
    assert len(upstream.calls) == 1
    assert "x-nan-auto-model" in response.headers


async def test_mid_stream_break_propagates_and_does_not_retry() -> None:
    async def breaking() -> AsyncIterator[bytes]:
        yield b'data: {"delta": "hi"}\n\n'
        raise ConnectionError("upstream broke mid-stream")

    upstream = ScriptedUpstream(
        lambda: httpx.Response(
            200, content=breaking(), headers={"content-type": "text/event-stream"}
        )
    )
    app = make_app(httpx.AsyncClient(transport=upstream))
    call, task = await start_call(app, chat_body())
    assert call.status == 200

    first = await call.first_body()
    assert first is not None and b"hi" in first
    await call.drain_body()  # nothing else may arrive
    await asyncio.wait_for(task, 2.0)
    assert len(call.errors) == 1  # the break propagated, it was not converted
    assert isinstance(call.errors[0], (httpx.HTTPError, ConnectionError))
    assert len(upstream.calls) == 1  # a broken stream is never retried


async def test_stream_request_body_is_rewritten_before_forwarding() -> None:
    upstream = ScriptedUpstream(
        lambda: httpx.Response(
            200, content=sse_stream(), headers={"content-type": "text/event-stream"}
        )
    )
    app = make_app(httpx.AsyncClient(transport=upstream))
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/completions", json=chat_body(), headers={"Authorization": CLIENT_KEY}
        )
    assert response.status_code == 200
    sent = json.loads(upstream.calls[0].content)
    registry = Registry(load_card())
    await registry.refresh()
    assert sent["model"] in registry.models or sent["model"] == DEFAULT_MODEL
    assert "nan_auto" not in sent
