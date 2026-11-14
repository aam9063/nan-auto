"""NAU-07 route tests: composition, `x-nan-auto-*` headers, passthrough, failures."""

import json
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
from nan_auto.router_core.features import extract_features
from nan_auto.router_core.policy import decide
from nan_auto.settings import Settings

UPSTREAM_CHAT = "https://api.nan.builders/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
CLIENT_KEY = "Bearer sk-client-canary-0"


class StaticProvider:
    """A `DecisionProvider` double that answers a canned decision and counts asks."""

    def __init__(self, decision: Decision | None = None, error: Exception | None = None) -> None:
        self.calls = 0
        self._decision = decision
        self._error = error

    async def decide(self, *, state: str, questions: dict) -> Decision:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._decision or canned_decision()


def canned_decision() -> Decision:
    """A low-effort chat_casual decision: the cheapest deterministic pick."""
    return Decision(
        decision_id="decision-canned",
        model="chat",
        task=ChoiceAnswer(label="chat_casual", probabilities={"chat_casual": 1.0}, confidence=1.0),
        difficulty=ScoreAnswer(
            score=0.5, legend={"0": "Trivial"}, probabilities={"0": 1.0}, confidence=1.0
        ),
        needs_reasoning=NoulAnswer(yes=0.0, confidence=1.0),
        expected_output_length=ScoreAnswer(
            score=0.5, legend={"0": "Short"}, probabilities={"0": 1.0}, confidence=1.0
        ),
    )


def make_app(provider: StaticProvider, settings: Settings | None = None) -> Any:  # noqa: ANN401
    """Build a gateway app on an offline (card-only) registry with a static Clef."""
    registry = Registry(load_card())
    return create_app(
        registry=registry,
        provider=provider,
        settings=settings or Settings(default_model=DEFAULT_MODEL),
    )


def chat_body(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "nan-auto",
        "messages": [{"role": "user", "content": "hello there"}],
        "temperature": 0.2,
    }
    body.update(extra)
    return body


async def expected_model(body: dict[str, Any], profile: str) -> str:
    """Recompute the expected election with the same inputs the route uses."""
    registry = Registry(load_card())
    await registry.refresh()
    result = decide(
        canned_decision(),
        extract_features(body),
        registry.models,
        profile=profile,
        default_model=DEFAULT_MODEL,
    )
    assert result.model is not None
    return result.model


async def send(app: Any, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/v1/chat/completions", json=body, headers=headers or {"Authorization": CLIENT_KEY}
        )


@respx.mock
async def test_alias_request_is_routed_and_annotated() -> None:
    provider = StaticProvider()
    app = make_app(provider)
    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, json={"id": "up-1", "object": "chat.completion"})
    )
    response = await send(app, chat_body(nan_auto={"profile": "nan-auto-fast"}))
    assert response.status_code == 200
    assert response.json() == {"id": "up-1", "object": "chat.completion"}
    assert provider.calls == 1

    request = upstream.calls.last.request
    sent = json.loads(request.content)
    expected = await expected_model(chat_body(), profile="nan-auto-fast")
    assert sent["model"] == expected
    assert "nan_auto" not in sent
    assert request.headers["authorization"] == CLIENT_KEY

    assert response.headers["x-nan-auto-model"] == expected
    # No store, so there is no detail to consult: the header is empty by
    # design (spec 8 says it is the id to look the detail up).
    assert response.headers["x-nan-auto-decision-id"] == ""
    assert response.headers["x-nan-auto-reason"]
    assert "x-nan-auto-fallback" not in response.headers  # only when a fallback happened


@respx.mock
async def test_profile_defaults_to_the_alias_when_nan_auto_absent() -> None:
    provider = StaticProvider()
    app = make_app(provider)
    upstream = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))
    response = await send(app, chat_body())
    assert response.status_code == 200
    expected = await expected_model(chat_body(), profile="nan-auto")
    assert json.loads(upstream.calls.last.request.content)["model"] == expected
    assert response.headers["x-nan-auto-model"] == expected


@respx.mock
async def test_non_alias_model_passes_through_byte_for_byte() -> None:
    provider = StaticProvider()
    app = make_app(provider)
    raw = json.dumps(
        {"model": "my-model", "messages": [{"role": "user", "content": "hi"}], "junk": "keep-me"}
    ).encode()
    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, content=b'{"id": "up-9"}')
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/chat/completions",
            content=raw,
            headers={"Authorization": CLIENT_KEY, "Content-Type": "application/json"},
        )
    assert response.status_code == 200
    assert response.content == b'{"id": "up-9"}'  # body relayed as received
    assert upstream.calls.last.request.content == raw  # outbound body untouched
    assert upstream.calls.last.request.headers["authorization"] == CLIENT_KEY
    assert provider.calls == 0  # no decision was made
    assert "x-nan-auto-model" not in response.headers
    assert "x-nan-auto-reason" not in response.headers


@respx.mock
@pytest.mark.parametrize(
    "nan_auto",
    [{"profile": "turbo"}, "yes", {"profile": "nan-auto", "bogus": 1}, {"explain": "true"}],
)
async def test_invalid_nan_auto_returns_400_and_calls_nobody(nan_auto: Any) -> None:
    provider = StaticProvider()
    app = make_app(provider)
    upstream = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={}))
    response = await send(app, chat_body(nan_auto=nan_auto))
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["message"]
    assert upstream.call_count == 0
    assert provider.calls == 0


@respx.mock
async def test_clef_failure_fails_open_to_the_default_model() -> None:
    provider = StaticProvider(error=ClefError("clef exploded"))
    app = make_app(provider)
    upstream = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))
    response = await send(app, chat_body())
    assert response.status_code == 200
    assert provider.calls == 1
    sent = json.loads(upstream.calls.last.request.content)
    assert sent["model"] == DEFAULT_MODEL
    assert response.headers["x-nan-auto-model"] == DEFAULT_MODEL
    assert response.headers["x-nan-auto-decision-id"] == ""
    assert response.headers["x-nan-auto-reason"]
    assert upstream.call_count == 1


@respx.mock
async def test_missing_user_message_fails_open_without_asking() -> None:
    provider = StaticProvider()
    app = make_app(provider)
    upstream = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={"id": "up-1"}))
    body = chat_body(messages=[{"role": "system", "content": "be brief"}])
    response = await send(app, body)
    assert response.status_code == 200
    assert provider.calls == 0
    assert json.loads(upstream.calls.last.request.content)["model"] == DEFAULT_MODEL


@respx.mock
async def test_failing_chat_body_returns_openai_shaped_400() -> None:
    provider = StaticProvider()
    app = make_app(provider)
    upstream = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={}))
    response = await send(app, {"model": "nan-auto"})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert upstream.call_count == 0
    assert provider.calls == 0


@respx.mock
async def test_upstream_failure_status_is_propagated_before_first_byte() -> None:
    provider = StaticProvider()
    app = make_app(provider)
    respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(
            503, json={"error": {"message": "overloaded", "type": "server_error"}}
        )
    )
    response = await send(app, chat_body())
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "overloaded"
    assert response.headers["x-nan-auto-model"]
