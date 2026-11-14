"""NAU-13 T6 wiring: one decision row per routed request, without blocking.

The gateway writes what the router decided and why through the public
`DecisionLogStore` API: model, level, source, timings, the Clef answers and
the one upstream attempt, using the store's own tag vocabularies. The rules
pinned here: a routed request leaves one row with its answers and attempts;
the conversation enters only as a hash, never as the raw `x-conversation-id`
value; pass-throughs (no decision made) write nothing; streaming records only
once the stream ends or fails, never before the first byte reaches the
client; and a raising store never breaks the response — it degrades to a
warning line with no content.

The streaming tests drive the app through the raw ASGI convention (same
reason as `test_proxy_secrets.py`) with an upstream gated on a test event, so
"written after the stream ends" is observed, not assumed.
"""

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import AsyncIterator, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport

from nan_auto.clef_client.errors import ClefError
from nan_auto.clef_client.types import ChoiceAnswer, Decision, NoulAnswer, ScoreAnswer
from nan_auto.gateway.app import create_app
from nan_auto.gateway.log_store import (
    Attempt,
    ClefAnswer,
    DecisionLogError,
    DecisionLogStore,
    DecisionRecord,
)
from nan_auto.registry.catalog import load_card
from nan_auto.registry.state import Registry
from nan_auto.settings import Settings

UPSTREAM_CHAT = "https://api.nan.builders/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
RAW_CONVERSATION = "client-chosen-conversation-raw-9f1"
CANARY_PROMPT = "PROMPT-CANARY-nau13-wiring"


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


class CountingStore(DecisionLogStore):
    """A real store that also counts `record_decision` calls."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.calls = 0

    async def record_decision(
        self,
        record: DecisionRecord,
        *,
        answers: Sequence[ClefAnswer] = (),
        attempts: Sequence[Attempt] = (),
    ) -> None:
        await super().record_decision(record, answers=answers, attempts=attempts)
        self.calls += 1


class FailingStore(DecisionLogStore):
    """A store whose writes always fail, as a broken database file would."""

    async def record_decision(
        self,
        record: DecisionRecord,
        *,
        answers: Sequence[ClefAnswer] = (),
        attempts: Sequence[Attempt] = (),
    ) -> None:
        raise DecisionLogError("disk on fire")


def make_app(provider: StaticProvider, store: DecisionLogStore | None = None) -> Any:  # noqa: ANN401
    registry = Registry(load_card())
    return create_app(
        registry=registry,
        provider=provider,
        settings=Settings(default_model=DEFAULT_MODEL),
        store=store,
    )


def chat_body(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "nan-auto",
        "messages": [{"role": "user", "content": "hello there"}],
    }
    body.update(extra)
    return body


async def post_json(app: Any, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:  # noqa: ANN401
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/v1/chat/completions", json=body, headers=headers)


async def post_raw(app: Any, raw: bytes) -> httpx.Response:  # noqa: ANN401
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/v1/chat/completions",
            content=raw,
            headers={"Content-Type": "application/json"},
        )


def _rows(db_path: Path, sql: str) -> list[sqlite3.Row]:
    """Query the decision log; an empty store (no table yet) reads as []."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql))
    except sqlite3.OperationalError as error:
        if "no such table" in str(error):
            return []
        raise
    finally:
        conn.close()


async def _wait_for_rows(db_path: Path, count: int, timeout: float = 2.0) -> list[sqlite3.Row]:
    """Wait until the scheduled write lands: it is fire-and-forget by design."""
    deadline = time.monotonic() + timeout
    rows = _rows(db_path, "SELECT * FROM REQUEST_LOG")
    while len(rows) < count and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
        rows = _rows(db_path, "SELECT * FROM REQUEST_LOG")
    return rows


async def _wait_for_calls(store: CountingStore, count: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while store.calls < count and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert store.calls == count


async def _settle() -> None:
    """Give the loop a few turns so an (absent) scheduled write would run."""
    for _ in range(20):
        await asyncio.sleep(0)


# --- one routed request, one decision row -------------------------------------


@respx.mock
async def test_routed_request_writes_one_decision_row_with_answers_and_attempts(
    tmp_path: Path,
) -> None:
    provider = StaticProvider()
    db = tmp_path / "decisions.db"
    app = make_app(provider, DecisionLogStore(db))
    respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, json={"id": "up-1", "choices": []})
    )
    response = await post_json(app, chat_body())
    assert response.status_code == 200
    assert response.headers["x-nan-auto-model"]

    rows = await _wait_for_rows(db, 1)
    assert len(rows) == 1
    decision = rows[0]
    assert decision["chosen_model"] == response.headers["x-nan-auto-model"]
    assert decision["reasoning_level"] == "off"
    assert decision["decision_source"] == "clef"
    assert decision["profile"] == "nan-auto"
    assert decision["clef_timeout"] == 800
    assert decision["clef_latency_ms"] >= 0
    assert decision["ttft_ms"] is None  # not a streaming request
    assert decision["total_ms"] is not None
    assert decision["prompt_tokens"] is None and decision["completion_tokens"] is None

    answers = _rows(
        db, "SELECT question_id, label, value, confidence FROM DECISION_ANSWER ORDER BY rowid"
    )
    assert [(a["question_id"], a["label"], a["value"], a["confidence"]) for a in answers] == [
        ("task", "chat_casual", 1.0, 1.0),
        ("difficulty", "Trivial", 0.5, 1.0),
        ("needs_reasoning", "false", 0.0, 1.0),
        ("expected_output_length", "Short", 0.5, 1.0),
    ]
    attempts = _rows(db, "SELECT * FROM ATTEMPT")
    assert len(attempts) == 1
    assert attempts[0]["model"] == decision["chosen_model"]
    assert attempts[0]["outcome"] == "success"
    assert attempts[0]["http_status"] == 200
    assert attempts[0]["latency_ms"] >= 0


@respx.mock
async def test_clef_failure_logs_the_fallback_source(tmp_path: Path) -> None:
    db = tmp_path / "decisions.db"
    app = make_app(StaticProvider(error=ClefError("clef exploded")), DecisionLogStore(db))
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))
    response = await post_json(app, chat_body())
    assert response.status_code == 200
    rows = await _wait_for_rows(db, 1)
    assert rows[0]["decision_source"] == "fallback"
    assert rows[0]["chosen_model"] == DEFAULT_MODEL
    assert rows[0]["reasoning_level"] == "off"


@respx.mock
async def test_the_conversation_is_stored_only_as_a_hash(tmp_path: Path) -> None:
    db = tmp_path / "decisions.db"
    app = make_app(StaticProvider(), DecisionLogStore(db))
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))
    response = await post_json(app, chat_body(), headers={"x-conversation-id": RAW_CONVERSATION})
    assert response.status_code == 200
    rows = await _wait_for_rows(db, 1)
    assert rows[0]["conversation_hash"] == sha256(RAW_CONVERSATION.encode("utf-8")).hexdigest()
    for table in ("REQUEST_LOG", "DECISION_ANSWER", "ATTEMPT"):
        for row in _rows(db, f"SELECT * FROM {table}"):
            assert RAW_CONVERSATION not in "".join(str(value) for value in tuple(row))


@respx.mock
async def test_passthrough_requests_write_no_row(tmp_path: Path) -> None:
    db = tmp_path / "decisions.db"
    provider = StaticProvider()
    app = make_app(provider, DecisionLogStore(db))
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))
    await post_json(
        app, {"model": "other-vendor-model", "messages": [{"role": "user", "content": "hi"}]}
    )
    await post_raw(app, b"{not json")
    await _settle()
    assert _rows(db, "SELECT * FROM REQUEST_LOG") == []
    assert provider.calls == 0  # no decision was made


# --- streaming records after the stream, never before the first byte ----------


async def _gated_stream_app(store: CountingStore, chunks: AsyncIterator[bytes]) -> Any:  # noqa: ANN401
    class GatedUpstream(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await request.aread()
            return httpx.Response(
                200, content=chunks, headers={"content-type": "text/event-stream"}
            )

    return create_app(
        registry=Registry(load_card()),
        provider=StaticProvider(),
        settings=Settings(default_model=DEFAULT_MODEL),
        client=httpx.AsyncClient(transport=GatedUpstream()),
        store=store,
    )


async def test_streaming_writes_its_row_only_after_the_stream_ends(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def gated() -> AsyncIterator[bytes]:
        yield b'data: {"delta": "a"}\n\n'
        await release.wait()
        yield b"data: [DONE]\n\n"

    db = tmp_path / "decisions.db"
    store = CountingStore(db)
    app = await _gated_stream_app(store, gated())
    call = AsgiCall()
    task = asyncio.create_task(run_asgi(app, call, chat_body(stream=True)))
    first = await asyncio.wait_for(call.first_body(), 2.0)
    assert first == b'data: {"delta": "a"}\n\n'
    assert store.calls == 0  # the write was not scheduled before the first byte
    release.set()
    body = await call.drain_body()
    assert b"[DONE]" in body
    await asyncio.wait_for(task, 2.0)
    await _wait_for_calls(store, 1)
    rows = await _wait_for_rows(db, 1)
    decision = rows[0]
    assert decision["ttft_ms"] is not None
    assert decision["total_ms"] is not None
    assert decision["total_ms"] >= decision["ttft_ms"]
    attempts = _rows(db, "SELECT * FROM ATTEMPT")
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "success"
    assert attempts[0]["http_status"] == 200


async def test_a_broken_stream_still_writes_its_row(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def broken() -> AsyncIterator[bytes]:
        yield b'data: {"delta": "a"}\n\n'
        await release.wait()
        raise ConnectionError("upstream broke")

    db = tmp_path / "decisions.db"
    store = CountingStore(db)
    app = await _gated_stream_app(store, broken())
    call = AsgiCall()
    task = asyncio.create_task(run_asgi(app, call, chat_body(stream=True)))
    assert await asyncio.wait_for(call.first_body(), 2.0) == b'data: {"delta": "a"}\n\n'
    release.set()
    await call.drain_body()
    await asyncio.wait_for(task, 2.0)
    assert len(call.errors) == 1  # the break propagated, as before
    await _wait_for_calls(store, 1)
    rows = await _wait_for_rows(db, 1)
    assert rows[0]["ttft_ms"] is not None
    attempts = _rows(db, "SELECT * FROM ATTEMPT")
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "http_error"
    assert attempts[0]["http_status"] == 200  # the exchange got a 200; delivery did not finish


# --- a raising store never breaks the response --------------------------------


@respx.mock
async def test_a_raising_store_never_breaks_the_response(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = make_app(StaticProvider(), FailingStore(tmp_path / "decisions.db"))
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))
    with caplog.at_level(logging.WARNING):
        response = await post_json(
            app, chat_body(messages=[{"role": "user", "content": CANARY_PROMPT}])
        )
    assert response.status_code == 200
    assert response.json() == {"id": "up-1"}  # the client still gets its answer
    # The write is scheduled fire-and-forget: give its warning time to land.
    deadline = time.monotonic() + 2.0
    while not any(record.levelname == "WARNING" for record in caplog.records):
        assert time.monotonic() < deadline, "the store failure never produced a warning"
        await asyncio.sleep(0.01)
    assert CANARY_PROMPT not in caplog.text  # the warning carries no content


@respx.mock
async def test_logging_disabled_keeps_the_route_as_today() -> None:
    provider = StaticProvider()
    app = make_app(provider, None)
    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, json={"id": "up-1", "choices": []})
    )
    response = await post_json(app, chat_body())
    assert response.status_code == 200
    assert response.headers["x-nan-auto-model"]
    assert provider.calls == 1
    assert upstream.call_count == 1


# --- raw ASGI harness (mirrors test_proxy_secrets.py) --------------------------


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
