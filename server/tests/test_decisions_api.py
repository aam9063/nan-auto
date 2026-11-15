"""NAU-16a read API tests (T1/T2/T5): decision history, detail and playground.

The dashboard reads what the router decided and why (spec 8, 10): the list is
newest first, the detail carries the Clef answers and the delivery attempts,
an unknown id is a 404 (never a 500), and with no store configured the list is
an empty list, not an error. The playground decides without any upstream model
call: features, Clef, the capability filter and the policy only. None of the
new routes ever returns prompt text or an API key (spec 12).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from httpx import ASGITransport

from nan_auto.clef_client.errors import ClefAuthError, ClefError
from nan_auto.clef_client.types import ChoiceAnswer, Decision, NoulAnswer, ScoreAnswer
from nan_auto.gateway.app import create_app
from nan_auto.gateway.log_store import (
    Attempt,
    ClefAnswer,
    DecisionLogStore,
    DecisionRecord,
)
from nan_auto.registry.catalog import load_card
from nan_auto.registry.state import Registry
from nan_auto.registry.types import CatalogEntry
from nan_auto.settings import Settings

BASE = datetime(2026, 12, 1, 12, 0, 0, tzinfo=UTC)
CANARY_PROMPT = "PROMPT-CANARY-nau16-dashboard"
CANARY_KEY = "sk-client-CANARY-KEY-nau16"
DEFAULT_MODEL = "deepseek-v4-flash"


class StaticProvider:
    """A `DecisionProvider` double that answers a canned decision and counts asks."""

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
                label="chat_casual",
                probabilities={"chat_casual": 1.0, "other": 0.0},
                confidence=0.9,
            ),
            difficulty=ScoreAnswer(
                score=0.5,
                legend={"0": "Trivial", "1": "Easy"},
                probabilities={"0": 0.9, "1": 0.1},
                confidence=0.8,
            ),
            needs_reasoning=NoulAnswer(yes=0.05, confidence=0.9),
            expected_output_length=ScoreAnswer(
                score=1.0,
                legend={"0": "Very short", "1": "Short"},
                probabilities={"1": 0.8, "0": 0.2},
                confidence=0.7,
            ),
        )


class RecordingUpstream(httpx.AsyncBaseTransport):
    """Upstream double that records every call and answers a minimal completion."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.calls.append(request)
        return httpx.Response(200, json={"id": "up-1", "choices": []})


def make_app(
    provider: StaticProvider | None = None,
    store: DecisionLogStore | None = None,
    upstream: RecordingUpstream | None = None,
) -> Any:  # noqa: ANN401
    client = httpx.AsyncClient(transport=upstream) if upstream is not None else None
    return create_app(
        registry=Registry(load_card()),
        provider=provider or StaticProvider(),
        settings=Settings(default_model=DEFAULT_MODEL),
        client=client,
        store=store,
    )


async def call(app: Any, method: str, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


async def seed(
    store: DecisionLogStore,
    request_id: str,
    *,
    created_at: datetime,
    answers: tuple[ClefAnswer, ...] = (),
    attempts: tuple[Attempt, ...] = (),
) -> None:
    """Write one decision row with a fixed timestamp, so ordering is observable."""
    record = DecisionRecord(
        request_id=request_id,
        conversation_hash="a" * 64,
        profile="nan-auto",
        reasoning_level="off",
        decision_source="clef",
        chosen_model="gemma4",
        clef_timeout=800,
        clef_latency_ms=120,
        ttft_ms=210,
        total_ms=980,
        created_at=created_at,
    )
    await store.record_decision(record, answers=answers, attempts=attempts)


# --- T1: GET /v1/decisions and GET /v1/decisions/{id} --------------------------


async def test_list_returns_decisions_newest_first(tmp_path: Any) -> None:  # noqa: ANN401
    store = DecisionLogStore(tmp_path / "decisions.db")
    await store.open()
    await seed(store, "req-old", created_at=BASE)
    await seed(store, "req-new", created_at=BASE + timedelta(seconds=2))
    await seed(store, "req-mid", created_at=BASE + timedelta(seconds=1))
    app = make_app(store=store)

    response = await call(app, "GET", "/v1/decisions")
    assert response.status_code == 200
    decisions = response.json()["decisions"]
    assert [d["id"] for d in decisions] == ["req-new", "req-mid", "req-old"]
    for entry in decisions:
        assert entry["chosen_model"] == "gemma4"
        assert entry["reasoning_level"] == "off"
        assert entry["decision_source"] == "clef"
        assert entry["profile"] == "nan-auto"
        assert entry["total_ms"] == 980
        assert entry["created_at"].startswith("2026-12-01T12:00:0")


async def test_list_limit_returns_only_the_newest(tmp_path: Any) -> None:  # noqa: ANN401
    store = DecisionLogStore(tmp_path / "decisions.db")
    await store.open()
    for index in range(4):
        await seed(store, f"req-{index}", created_at=BASE + timedelta(seconds=index))
    app = make_app(store=store)

    response = await call(app, "GET", "/v1/decisions?limit=2")
    assert response.status_code == 200
    assert [d["id"] for d in response.json()["decisions"]] == ["req-3", "req-2"]


async def test_detail_returns_answers_and_attempts(tmp_path: Any) -> None:  # noqa: ANN401
    store = DecisionLogStore(tmp_path / "decisions.db")
    await store.open()
    answers = (
        ClefAnswer("task", "chat_casual", 1.0, 0.9),
        ClefAnswer("difficulty", "Trivial", 0.5, 0.8),
    )
    attempts = (
        Attempt("glm5.3-flash", "http_error", 503, 1400),
        Attempt("gemma4", "success", 200, 980),
    )
    await seed(store, "req-1", created_at=BASE, answers=answers, attempts=attempts)
    app = make_app(store=store)

    response = await call(app, "GET", "/v1/decisions/req-1")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "req-1"
    assert body["answers"] == [
        {"question_id": "task", "label": "chat_casual", "value": 1.0, "confidence": 0.9},
        {"question_id": "difficulty", "label": "Trivial", "value": 0.5, "confidence": 0.8},
    ]
    assert body["attempts"] == [
        {"model": "glm5.3-flash", "outcome": "http_error", "http_status": 503, "latency_ms": 1400},
        {"model": "gemma4", "outcome": "success", "http_status": 200, "latency_ms": 980},
    ]


async def test_detail_of_unknown_id_is_404(tmp_path: Any) -> None:  # noqa: ANN401
    store = DecisionLogStore(tmp_path / "decisions.db")
    await store.open()
    app = make_app(store=store)
    response = await call(app, "GET", "/v1/decisions/nope")
    assert response.status_code == 404


async def test_without_store_the_list_is_empty_and_detail_is_404() -> None:
    app = make_app(store=None)
    response = await call(app, "GET", "/v1/decisions")
    assert response.status_code == 200
    assert response.json()["decisions"] == []
    assert (await call(app, "GET", "/v1/decisions/anything")).status_code == 404


# --- T2: POST /v1/playground/decide --------------------------------------------


async def test_playground_decides_without_touching_the_upstream() -> None:
    provider = StaticProvider()
    upstream = RecordingUpstream()
    app = make_app(provider=provider, upstream=upstream)
    response = await call(
        app,
        "POST",
        "/v1/playground/decide",
        json={"messages": [{"role": "user", "content": "hello there"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] in body["candidates"]
    assert body["reasoning_level"] in ("off", "low", "medium", "high")
    assert body["reason"]
    assert body["decision_source"] == "clef"
    probabilities = body["probabilities"]
    assert set(probabilities) == {"task", "difficulty", "needs_reasoning", "expected_output_length"}
    assert probabilities["task"]["label"] == "chat_casual"
    assert probabilities["task"]["probabilities"]["chat_casual"] == 1.0
    assert probabilities["needs_reasoning"]["yes"] == 0.05
    for rejected in body["rejected"]:
        assert set(rejected) == {"model", "reason"}
        assert rejected["model"] != body["model"]
    assert upstream.calls == []  # zero upstream model calls: decide only
    assert provider.calls == 1  # Clef was asked, once


async def test_playground_fails_open_without_clef(tmp_path: Any) -> None:  # noqa: ANN401
    provider = StaticProvider(error=ClefError("clef down"))
    app = make_app(provider=provider)
    response = await call(
        app,
        "POST",
        "/v1/playground/decide",
        json={"messages": [{"role": "user", "content": "hello there"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == DEFAULT_MODEL
    assert body["decision_source"] == "fallback"
    assert body["probabilities"] == {}
    assert body["rejected"] == []


async def test_fail_open_reason_names_the_cause_generic_clef_error() -> None:
    """The fail-open reason says why Clef was skipped, not just that it was."""
    provider = StaticProvider(error=ClefError("clef down"))
    app = make_app(provider=provider)
    response = await call(
        app,
        "POST",
        "/v1/playground/decide",
        json={"messages": [{"role": "user", "content": "hello there"}]},
    )
    assert response.json()["reason"] == "clef_error: clef down"


async def test_fail_open_reason_says_auth_when_clef_rejects_the_key() -> None:
    """A rejected key is a different incident from a dead Clef: say so.

    Both fail open to the same default model, but the dashboard shows the
    reason verbatim — an auth failure must not masquerade as downtime.
    """
    provider = StaticProvider(error=ClefAuthError("Clef rejected the API key (401)"))
    app = make_app(provider=provider)
    response = await call(
        app,
        "POST",
        "/v1/playground/decide",
        json={"messages": [{"role": "user", "content": "hello there"}]},
    )
    body = response.json()
    assert body["decision_source"] == "fallback"
    assert body["reason"] == "clef_auth_error: Clef rejected the API key (401)"


async def test_playground_rejects_a_body_without_messages() -> None:
    app = make_app()
    response = await call(app, "POST", "/v1/playground/decide", json={"messages": "not a list"})
    assert response.status_code == 422


# --- T5: no new route leaks prompt text or a key -------------------------------


async def test_no_new_route_leaks_prompt_or_key(tmp_path: Any) -> None:  # noqa: ANN401
    """Canaries ride the prompt and the credential; every reply is swept."""
    provider = StaticProvider()
    store = DecisionLogStore(tmp_path / "decisions.db")
    await store.open()
    app = make_app(provider=provider, store=store, upstream=RecordingUpstream())

    routed = await call(
        app,
        "POST",
        "/v1/chat/completions",
        json={"model": "nan-auto", "messages": [{"role": "user", "content": CANARY_PROMPT}]},
        headers={"Authorization": CANARY_KEY},
    )
    assert routed.status_code == 200
    playground = await call(
        app,
        "POST",
        "/v1/playground/decide",
        json={"messages": [{"role": "user", "content": CANARY_PROMPT}]},
        headers={"Authorization": CANARY_KEY},
    )
    assert playground.status_code == 200
    listing = await call(app, "GET", "/v1/decisions")
    decisions = listing.json()["decisions"]
    assert decisions, "the sweep is only meaningful over real stored decisions"
    detail = await call(app, "GET", f"/v1/decisions/{decisions[0]['id']}")
    missing = await call(app, "GET", "/v1/decisions/unknown-id")

    for response in (routed, playground, listing, detail, missing):
        assert CANARY_PROMPT not in response.text, response.url.path
        assert CANARY_KEY not in response.text, response.url.path
    # The playground's probabilities are typed facts, not the prompt it read.
    assert playground.json()["probabilities"]["task"]["label"] == "chat_casual"


async def test_the_playground_sees_the_same_inventory_as_the_routed_path(
    tmp_path: Path,
) -> None:
    """A cold registry must not make the playground decide against nothing.

    The inventory refreshes lazily; without a refresh the playground rejects
    nobody, chooses nobody and fails open, which is exactly the screen the
    dashboard is meant to explain.
    """
    fetched = (
        CatalogEntry(id="gemma4", mode="chat", max_input_tokens=262144),
        CatalogEntry(id="deepseek-v4-flash", mode="chat", max_input_tokens=1048576),
    )

    async def fetch_catalog() -> tuple[CatalogEntry, ...]:
        return fetched

    registry = Registry(load_card(), fetcher=fetch_catalog, ttl_seconds=3600.0)
    app = create_app(
        registry=registry, provider=StaticProvider(), settings=Settings(default_model=DEFAULT_MODEL)
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/playground/decide",
            json={"model": "nan-auto", "messages": [{"role": "user", "content": "hola"}]},
            headers={"Authorization": CANARY_KEY},
        )
    assert response.status_code == 200
    payload = response.json()
    assert "fallback=" not in payload["reason"]  # a real decision, not a fail-open
    # NAU-18 rows prefer deepseek-v4-flash (the fastest measured cell), but the
    # honour-off penalty re-elects qwen3.8-flash for off requests: the penalty
    # stacks on a measured latency that already includes deepseek's reasoning.
    # Follow-up: penalty-vs-measured-latency arbitration (odd/tasks/nau-18-policy-v2.md).
    assert payload["model"] == "qwen3.8-flash"
    assert payload["candidates"]  # the ranking came from a populated inventory
    assert registry.models  # and the refresh actually happened


async def test_the_decision_id_header_resolves_through_the_detail_route(tmp_path: Path) -> None:
    """Spec 8 calls this header "id para consultar el detalle"; it must resolve.

    It used to carry the Clef decision id, which is not a log key, so every
    documented lookup ended in a 404.
    """
    store = DecisionLogStore(tmp_path / "log.sqlite")
    await store.open()
    upstream = RecordingUpstream()
    app = create_app(
        registry=Registry(load_card()),
        provider=StaticProvider(),
        settings=Settings(default_model=DEFAULT_MODEL),
        client=httpx.AsyncClient(transport=upstream),
        store=store,
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        routed = await client.post(
            "/v1/chat/completions",
            json={"model": "nan-auto", "messages": [{"role": "user", "content": "hola"}]},
            headers={"Authorization": CANARY_KEY},
        )
        decision_id = routed.headers["x-nan-auto-decision-id"]
        assert decision_id and decision_id != "decision-canned"

        detail = await client.get(f"/v1/decisions/{decision_id}")

    assert detail.status_code == 200
    assert detail.json()["id"] == decision_id
    assert len(detail.json()["answers"]) == 4


async def test_a_failed_log_write_publishes_no_event(tmp_path: Path) -> None:
    """A subscriber must never see a decision the log does not hold."""

    class ExplodingStore(DecisionLogStore):
        async def record_decision(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            raise RuntimeError("disk full")

    store = ExplodingStore(tmp_path / "boom.sqlite")
    await store.open()
    upstream = RecordingUpstream()
    app = create_app(
        registry=Registry(load_card()),
        provider=StaticProvider(),
        settings=Settings(default_model=DEFAULT_MODEL),
        client=httpx.AsyncClient(transport=upstream),
        store=store,
    )
    bus = app.state.decision_bus
    subscription = bus.subscribe()
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            routed = await client.post(
                "/v1/chat/completions",
                json={"model": "nan-auto", "messages": [{"role": "user", "content": "hola"}]},
                headers={"Authorization": CANARY_KEY},
            )
        assert routed.status_code == 200  # a log failure never breaks the reply
        assert subscription.queue.empty()  # and it publishes nothing
    finally:
        bus.unsubscribe(subscription)


async def test_the_stream_event_carries_the_same_timestamp_as_the_row(tmp_path: Path) -> None:
    """The event leaves `created_at` null if it is built before the store stamps it."""
    store = DecisionLogStore(tmp_path / "ts.sqlite")
    await store.open()
    upstream = RecordingUpstream()
    app = create_app(
        registry=Registry(load_card()),
        provider=StaticProvider(),
        settings=Settings(default_model=DEFAULT_MODEL),
        client=httpx.AsyncClient(transport=upstream),
        store=store,
    )
    bus = app.state.decision_bus
    subscription = bus.subscribe()
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            await client.post(
                "/v1/chat/completions",
                json={"model": "nan-auto", "messages": [{"role": "user", "content": "hola"}]},
                headers={"Authorization": CANARY_KEY},
            )
            frame = subscription.queue.get_nowait()
            # The bus publishes complete SSE frames: strip the envelope first.
            payload = json.loads(frame.split(b"data: ", 1)[1].strip())
            stored = (await store.list_decisions())[0]
    finally:
        bus.unsubscribe(subscription)

    assert payload["created_at"] is not None
    assert payload["created_at"] == stored.decision.created_at
