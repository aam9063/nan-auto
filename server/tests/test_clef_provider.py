"""Contract tests for the `nan` and `mock` DecisionProviders (NAU-02-T4, T5)."""

import asyncio

import httpx
import pytest
import respx

from nan_auto.clef_client.base import DecisionProvider
from nan_auto.clef_client.mock import MockDecisionProvider
from nan_auto.clef_client.nan import (
    ClefAuthError,
    ClefError,
    ClefUnavailable,
    NanDecisionProvider,
)
from nan_auto.clef_client.parser import ClefFormatError
from nan_auto.clef_client.questions import build_questions

URL = "https://api.nan.builders/v1/systemone"
PAYLOAD = {
    "id": "decision-test",
    "object": "structured_decision",
    "model": "clef",
    "answers": {
        "task": {
            "type": "choice",
            "choice": "coding_debug_or_review",
            "probabilities": {"coding_debug_or_review": 0.96, "other": 0.04},
            "confidence": 0.96,
        },
        "difficulty": {
            "type": "score",
            "score": 2.9,
            "legend": {"0": "Trivial", "1": "Easy", "2": "Medium", "3": "Hard", "4": "Expert"},
            "probabilities": {"2": 0.37, "3": 0.27, "4": 0.33},
            "confidence": 0.37,
        },
        "needs_reasoning": {
            "type": "noul",
            "noul": 0.83,
            "probabilities": {"yes": 0.83, "no": 0.17},
            "confidence": 0.83,
        },
        "expected_output_length": {
            "type": "score",
            "score": 1.98,
            "legend": {"0": "Very short", "1": "Short", "2": "Medium", "3": "Long"},
            "probabilities": {"2": 0.63},
            "confidence": 0.63,
        },
    },
    "usage": {"input_tokens": 518, "output_tokens": 0},
}


def provider(**kwargs) -> NanDecisionProvider:
    client = httpx.AsyncClient()
    return NanDecisionProvider(client=client, api_key="sk-test", url=URL, **kwargs)


@respx.mock
async def test_returns_parsed_decision_on_200() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    decision = await provider().decide(state="{}", questions=build_questions())
    assert decision.task.label == "coding_debug_or_review"
    assert decision.difficulty.level() == 3


@respx.mock
async def test_sends_model_clef_and_bearer_token() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    await provider().decide(state='{"meta":{}}', questions=build_questions())
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer sk-test"
    assert b'"model":"clef"' in request.content
    assert b'"state":"{\\"meta\\":{}}"' in request.content


@respx.mock
async def test_retries_immediately_on_429_then_succeeds() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": {"message": "too many in flight"}}),
            httpx.Response(200, json=PAYLOAD),
        ]
    )
    decision = await provider(retry_backoff=0.0).decide(state="{}", questions=build_questions())
    assert decision.model == "clef"
    assert route.call_count == 2


@respx.mock
async def test_sustained_429_raises_unavailable() -> None:
    respx.post(URL).mock(return_value=httpx.Response(429, json={"error": {}}))
    with pytest.raises(ClefUnavailable, match="429"):
        await provider(retry_backoff=0.0, max_retries=2).decide(
            state="{}", questions=build_questions()
        )


@respx.mock
async def test_401_raises_auth_error_without_retry() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(401, json={"error": {}}))
    with pytest.raises(ClefAuthError):
        await provider(retry_backoff=0.0).decide(state="{}", questions=build_questions())
    assert route.call_count == 1


@respx.mock
async def test_timeout_raises_unavailable_for_fail_open() -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ClefUnavailable, match="timeout"):
        await provider(timeout=0.01).decide(state="{}", questions=build_questions())


@respx.mock
async def test_server_error_raises_unavailable() -> None:
    respx.post(URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ClefUnavailable, match="500"):
        await provider().decide(state="{}", questions=build_questions())


@respx.mock
async def test_200_with_non_json_body_raises_unavailable() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, text="<html>under maintenance</html>"))
    with pytest.raises(ClefUnavailable, match="non-JSON"):
        await provider().decide(state="{}", questions=build_questions())


@respx.mock
async def test_200_with_json_list_body_raises_format_error() -> None:
    # A wrong-shaped body is a format failure, not a crash or an availability lie.
    respx.post(URL).mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    with pytest.raises(ClefFormatError):
        await provider().decide(state="{}", questions=build_questions())


@respx.mock
async def test_api_key_never_appears_in_exception_messages() -> None:
    # The key travels only in the Authorization header: no error path may echo it.
    canary = "sk-CANARY-9911"
    client = httpx.AsyncClient()
    prov = NanDecisionProvider(
        client=client,
        api_key=canary,
        url=URL,
        retry_backoff=0.0,
        max_retries=2,
        timeout=0.01,
    )
    cases = {
        "timeout": httpx.ReadTimeout("slow"),
        "401": httpx.Response(401, json={"error": {"message": "invalid key"}}),
        "403": httpx.Response(403, text="forbidden for this principal"),
        "403-echoing-headers": httpx.Response(403, text=f"denied for authorization={canary}"),
        "429": httpx.Response(429, json={"error": {"message": "too many in flight"}}),
        "500": httpx.Response(500, text="boom"),
        "non-json": httpx.Response(200, text="<html>under maintenance</html>"),
    }
    for name, side_effect in cases.items():
        respx.post(URL).mock(side_effect=side_effect)
        with pytest.raises(ClefError) as exc_info:
            await prov.decide(state="{}", questions=build_questions())
        assert canary not in str(exc_info.value), f"canary leaked in the {name} path"


@respx.mock
async def test_4xx_body_redacted_even_when_key_straddles_truncation() -> None:
    # Redaction must happen before the 200-char cap: a key starting at column
    # 187 would otherwise lose its head in the slice and survive redaction.
    key = "sk-FAKE9KEY9BOUNDARY9TEST"
    body = "x" * 187 + key
    assert len(key) > 200 - 187  # the truncation would cut into the key
    respx.post(URL).mock(return_value=httpx.Response(400, text=body))
    client = httpx.AsyncClient()
    prov = NanDecisionProvider(client=client, api_key=key, url=URL)
    with pytest.raises(ClefUnavailable) as exc_info:
        await prov.decide(state="{}", questions=build_questions())
    text = str(exc_info.value)
    assert key not in text
    # No prefix of the key longer than 3 characters may survive either.
    leaked = [n for n in range(4, len(key) + 1) if key[:n] in text]
    assert not leaked, f"prefixes of the key leaked: {[key[:n] for n in leaked]}"


@respx.mock
async def test_decide_never_escapes_a_non_clef_error() -> None:
    # Fail-open (NAU-11) relies on decide() raising only ClefError subclasses.
    import json as _json

    nan_body = {
        **PAYLOAD,
        "answers": {
            **PAYLOAD["answers"],
            "difficulty": {
                **PAYLOAD["answers"]["difficulty"],
                "score": float("nan"),
            },
        },
    }
    # Python's json.dumps emits bare NaN for non-finite floats; send it as raw
    # text exactly as a lenient server could (httpx refuses to serialize it).
    nan_text = _json.dumps(nan_body)
    assert "NaN" in nan_text
    canary_body = {"error": {"message": "authorization=sk-CANARY-9911", "type": "BadRequestError"}}
    cases = {
        "nan-body": httpx.Response(
            200, text=nan_text, headers={"content-type": "application/json"}
        ),
        "canary-error-body": httpx.Response(200, json=canary_body),
    }
    for name, response in cases.items():
        respx.post(URL).mock(return_value=response)
        with pytest.raises(ClefError) as exc_info:
            await provider().decide(state="{}", questions=build_questions())
        assert isinstance(exc_info.value, ClefError), f"{name} escaped as non-ClefError"
        assert "sk-CANARY-9911" not in str(exc_info.value), f"canary leaked in the {name} path"


@respx.mock
async def test_never_exceeds_max_in_flight() -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, json=PAYLOAD)

    respx.post(URL).mock(side_effect=handler)
    prov = provider(max_in_flight=2)
    await asyncio.gather(*(prov.decide(state="{}", questions={}) for _ in range(6)))
    assert peak <= 2


async def test_mock_provider_satisfies_the_same_contract() -> None:
    mock: DecisionProvider = MockDecisionProvider()
    decision = await mock.decide(
        state='{"last_user_message_head":"Explain this stack trace"}', questions={}
    )
    assert decision.task.label == "coding_debug_or_review"
    assert decision.difficulty.level() >= 2
    assert decision.needs_reasoning.is_positive


async def test_mock_provider_classifies_casual_chat_as_easy() -> None:
    decision = await MockDecisionProvider().decide(
        state='{"last_user_message_head":"Holaaa, que tal el finde? Contame algo."}', questions={}
    )
    assert decision.task.label == "chat_casual"
    assert decision.difficulty.level() <= 1
    assert decision.needs_reasoning.is_negative


async def test_mock_provider_never_touches_the_network() -> None:
    # No respx mock active: any HTTP call would raise.
    decision = await MockDecisionProvider().decide(
        state='{"last_user_message_head":"hola"}', questions={}
    )
    assert decision.model == "mock"
