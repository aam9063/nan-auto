"""NAU-07/08 secret hygiene: the client's `Authorization` key touches NaN alone.

Every path here sends the canary as the client key and then proves the key
shows up in exactly one place: the outbound `Authorization` header to the chat
upstream. It must never appear in a response body, a response header, or an
exception message.

The broken-stream test drives the app through the raw ASGI convention because
`httpx.ASGITransport` buffers whole bodies, hiding both the partial chunks and
the relay's exception.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport

from nan_auto.clef_client.errors import ClefError
from nan_auto.clef_client.types import ChoiceAnswer, Decision, NoulAnswer, ScoreAnswer
from nan_auto.gateway.app import create_app
from nan_auto.registry.catalog import load_card
from nan_auto.registry.state import Registry
from nan_auto.settings import Settings

UPSTREAM_CHAT = "https://api.nan.builders/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
CANARY = "sk-canary-sup3r-s3cr3t-42"
AUTH = {"Authorization": f"Bearer {CANARY}"}


class StaticProvider:
    """A `DecisionProvider` double; optionally raises to force a fail-open run."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self._error = error

    async def decide(self, *, state: str, questions: dict) -> Decision:
        self.calls += 1
        if self._error is not None:
            raise self._error
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


def make_app(provider: StaticProvider) -> Any:  # noqa: ANN401
    registry = Registry(load_card())
    return create_app(
        registry=registry,
        provider=provider,
        settings=Settings(default_model=DEFAULT_MODEL),
    )


def chat_body(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "nan-auto",
        "messages": [{"role": "user", "content": "hello there"}],
    }
    body.update(extra)
    return body


def assert_key_absent(response: httpx.Response) -> None:
    assert CANARY not in response.text
    for header_name, value in response.headers.items():
        assert CANARY not in value, f"canary leaked in response header {header_name}"


async def post(app: Any, body: Any) -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/v1/chat/completions", json=body, headers=AUTH)


@respx.mock
async def test_success_replies_without_the_key() -> None:
    provider = StaticProvider()
    app = make_app(provider)
    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, json={"id": "up-1", "choices": []})
    )
    response = await post(app, chat_body())
    assert response.status_code == 200
    assert_key_absent(response)
    # The only sanctioned path: inbound header -> upstream header.
    assert upstream.calls.last.request.headers["authorization"] == f"Bearer {CANARY}"
    assert isinstance(provider.calls, int)


@respx.mock
async def test_upstream_4xx_does_not_leak_the_key() -> None:
    app = make_app(StaticProvider())
    respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "bad request", "type": "invalid_request_error"}}
        )
    )
    response = await post(app, chat_body())
    assert response.status_code == 400
    assert_key_absent(response)


@respx.mock
async def test_upstream_5xx_does_not_leak_the_key() -> None:
    app = make_app(StaticProvider())
    respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(
            500, json={"error": {"message": "boom", "type": "server_error"}}
        )
    )
    response = await post(app, chat_body())
    assert response.status_code == 500
    assert_key_absent(response)


@respx.mock
async def test_clef_failure_does_not_leak_the_key() -> None:
    app = make_app(StaticProvider(error=ClefError("clef blew up")))
    respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, json={"id": "up-1", "choices": []})
    )
    response = await post(app, chat_body())
    assert response.status_code == 200
    assert_key_absent(response)


@respx.mock
async def test_invalid_nan_auto_reply_does_not_leak_the_key() -> None:
    app = make_app(StaticProvider())
    upstream = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={}))
    response = await post(app, chat_body(nan_auto={"profile": "nope"}))
    assert response.status_code == 400
    assert_key_absent(response)
    assert upstream.call_count == 0


@respx.mock
async def test_stream_success_does_not_leak_the_key() -> None:
    app = make_app(StaticProvider())

    async def stream() -> AsyncIterator[bytes]:
        yield b'data: {"delta": "hi"}\n\n'
        yield b"data: [DONE]\n\n"

    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(
            200, content=stream(), headers={"content-type": "text/event-stream"}
        )
    )
    transport = ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
        client.stream(
            "POST", "/v1/chat/completions", json=chat_body(stream=True), headers=AUTH
        ) as response,
    ):
        assert response.status_code == 200
        body = b""
        async for chunk in response.aiter_raw():
            body += chunk
    assert b"[DONE]" in body
    assert CANARY.encode() not in body
    for _name, value in response.headers.items():
        assert CANARY not in value
    assert upstream.calls.last.request.headers["authorization"] == f"Bearer {CANARY}"


@respx.mock
async def test_broken_stream_does_not_leak_the_key_in_the_exception() -> None:
    app = make_app(StaticProvider())

    async def broken() -> AsyncIterator[bytes]:
        yield b'data: {"delta": "hi"}\n\n'
        raise ConnectionError("upstream broke")

    class ScriptedUpstream(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.calls: list[httpx.Request] = []

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await request.aread()
            self.calls.append(request)
            return httpx.Response(
                200, content=broken(), headers={"content-type": "text/event-stream"}
            )

    upstream = ScriptedUpstream()
    app = create_app(
        registry=Registry(load_card()),
        provider=StaticProvider(),
        settings=Settings(default_model=DEFAULT_MODEL),
        client=httpx.AsyncClient(transport=upstream),
    )
    call = AsgiCall()
    task = asyncio.create_task(run_asgi(app, call, chat_body(stream=True)))
    await asyncio.wait_for(call.status_seen.wait(), 2.0)
    assert call.status == 200
    assert await call.first_body() == b'data: {"delta": "hi"}\n\n'
    await call.drain_body()
    await asyncio.wait_for(task, 2.0)
    assert len(upstream.calls) == 1  # a broken stream is never retried
    assert len(call.errors) == 1  # the break propagated
    assert CANARY not in str(call.errors[0])


class AsgiCall:
    """Collects the status, headers, body chunks and error of one ASGI request."""

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


async def run_asgi(app: Any, call: AsgiCall, body: dict[str, Any]) -> None:  # noqa: ANN401
    """Send one raw ASGI request and collect response messages into `call`."""
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
            (b"authorization", f"Bearer {CANARY}".encode()),
        ],
    }
    request_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if request_sent:
            # Keep the response open until streaming ends; an instant disconnect
            # would cancel the body relay mid-stream.
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

    try:
        await app(scope, receive, send)
    except BaseException as error:  # noqa: BLE001 - the relay must propagate raw
        call.errors.append(error)
    finally:
        call.body_done.set()
        await call.bodies.put(None)


@respx.mock
async def test_transport_failure_replies_openai_shaped_without_the_key() -> None:
    app = make_app(StaticProvider())
    respx.post(UPSTREAM_CHAT).mock(side_effect=httpx.ConnectError("refused"))
    response = await post(app, chat_body())
    assert response.status_code == 502
    assert_key_absent(response)
    payload = response.json()
    assert "message" in payload["error"] and payload["error"]["type"]


# --- regression pins from the NAU-13 wiring review -----------------------------
# New pins only: each one proves an existing behavior a verifier found untested.
# If one of these fails, it is a defect, not a test to adjust.


@respx.mock
async def test_non_alias_passthrough_keeps_the_key_off_every_surface() -> None:
    app = make_app(StaticProvider())
    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, json={"id": "up-1", "choices": []})
    )
    response = await post(
        app, {"model": "other-vendor-model", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert response.status_code == 200
    assert_key_absent(response)
    # The only sanctioned path: inbound header -> upstream header, verbatim.
    assert upstream.calls.last.request.headers["authorization"] == f"Bearer {CANARY}"


@respx.mock
async def test_unparseable_body_keeps_the_key_off_every_surface() -> None:
    app = make_app(StaticProvider())
    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, json={"id": "up-1", "choices": []})
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/completions",
            content=b'{"model": oops',
            headers={**AUTH, "Content-Type": "application/json"},
        )
    assert response.status_code == 200
    assert_key_absent(response)
    assert upstream.calls.last.request.headers["authorization"] == f"Bearer {CANARY}"


@respx.mock
async def test_non_alias_passthrough_hides_a_failing_transport_exception_text() -> None:
    # The transport error itself carries the canary in its message; nothing that
    # surfaces to the client (body, headers or exception text) may echo it.
    app = make_app(StaticProvider())
    respx.post(UPSTREAM_CHAT).mock(
        side_effect=httpx.ConnectError(f"refused while authenticating {CANARY}")
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "other-vendor-model", "messages": [{"role": "user", "content": "hi"}]},
            headers=AUTH,
        )
    assert response.status_code == 502
    assert_key_absent(response)


@respx.mock
async def test_unparseable_body_hides_a_failing_transport_exception_text() -> None:
    app = make_app(StaticProvider())
    respx.post(UPSTREAM_CHAT).mock(
        side_effect=httpx.ConnectError(f"refused while authenticating {CANARY}")
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/completions",
            content=b'{"model": oops',
            headers={**AUTH, "Content-Type": "application/json"},
        )
    assert response.status_code == 502
    assert_key_absent(response)


@respx.mock
async def test_passthrough_forwards_a_body_carrying_its_own_nan_auto_byte_for_byte() -> None:
    provider = StaticProvider()
    app = make_app(provider)
    raw = json.dumps(
        {
            "model": "other-vendor-model",
            "messages": [{"role": "user", "content": "hi"}],
            "nan_auto": {"profile": "zeta", "bogus": True},
        }
    ).encode()
    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, json={"id": "up-1", "choices": []})
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/completions",
            content=raw,
            headers={**AUTH, "Content-Type": "application/json"},
        )
    assert response.status_code == 200
    assert upstream.calls.last.request.content == raw  # byte-for-byte, nan_auto kept
    assert upstream.calls.last.request.headers["authorization"] == f"Bearer {CANARY}"
    assert provider.calls == 0  # no decision was made


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_upstream_error_status_does_not_leak_the_key(status: int) -> None:
    app = make_app(StaticProvider())
    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(
            status, json={"error": {"message": "upstream said no", "type": "server_error"}}
        )
    )
    response = await post(app, chat_body())
    assert response.status_code == status
    assert_key_absent(response)
    assert upstream.calls.last.request.headers["authorization"] == f"Bearer {CANARY}"
