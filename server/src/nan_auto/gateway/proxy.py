"""The `/v1/chat/completions` gateway route (NAU-07 proxy, NAU-08 streaming).

One route, two behaviors (spec 5, 8, 12 and 14):

- An alias model (`nan-auto`, `nan-auto-fast`, `nan-auto-quality`) triggers the
  routing path: validate `nan_auto`, strip it, extract features, ask Clef,
  elect a model with the policy, merge the reasoning parameters and forward a
  rewritten body with the real model id. Four `x-nan-auto-*` headers annotate
  every routed reply, streaming or not.
- Any other model is a byte-for-byte pass-through: no decision, no header, no
  body rewrite.

Relay rules: without `stream` the upstream body is forwarded as received; with
`stream` bytes are relayed as they arrive, never buffered. An upstream error
before the first byte propagates with its status; once the stream has started
a break propagates and is never retried.

Secret hygiene: the client's `Authorization` header is forwarded to the chat
upstream and touches nothing else — it is never stored, logged, echoed back or
embedded in an error message.
"""

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import ValidationError

from nan_auto.clef_client.base import DecisionProvider
from nan_auto.clef_client.errors import ClefError
from nan_auto.clef_client.questions import build_questions, build_state
from nan_auto.registry.state import Registry
from nan_auto.router_core.features import FeatureExtractionError, extract_features
from nan_auto.router_core.policy import decide
from nan_auto.router_core.reasoning import reasoning_params
from nan_auto.schema.alias import ROUTER_ALIASES
from nan_auto.schema.nan_auto_options import NanAutoOptions
from nan_auto.settings import Settings

_CHAT_COMPLETIONS_PATH = "/chat/completions"
_JSON_MEDIA_TYPE = "application/json"
_SSE_MEDIA_TYPE = "text/event-stream"


def proxy_router(
    registry: Registry,
    provider: DecisionProvider,
    settings: Settings,
    client: httpx.AsyncClient,
) -> APIRouter:
    """Build the chat-completions route over one registry, Clef and upstream client."""
    router = APIRouter()
    upstream_url = f"{settings.nan_base_url}{_CHAT_COMPLETIONS_PATH}"

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        authorization = request.headers.get("authorization", "")
        raw = await request.body()
        try:
            body = json.loads(raw)
        except ValueError:
            body = None

        if not (isinstance(body, dict) and isinstance(body.get("model"), str)):
            return await _passthrough(raw, authorization, request, client, upstream_url, None)
        if body["model"] not in ROUTER_ALIASES:
            return await _passthrough(raw, authorization, request, client, upstream_url, body)

        try:
            raw_options = body.get("nan_auto")
            options = (
                NanAutoOptions.model_validate(raw_options)
                if raw_options is not None
                else NanAutoOptions()
            )
        except ValidationError as error:
            return _error_response(400, _options_message(error))

        outbound = {key: value for key, value in body.items() if key != "nan_auto"}
        try:
            features = extract_features(
                outbound, conversation_id=request.headers.get("x-conversation-id")
            )
        except FeatureExtractionError as error:
            return _error_response(400, str(error))

        await registry.refresh_if_stale()
        decision = None
        last_user_text = _last_user_text(outbound)
        if last_user_text:
            try:
                state = build_state(
                    last_user_text, system_prompt=_system_prompt(outbound), turns=features.turns
                )
                decision = await provider.decide(state=state, questions=build_questions())
            except ClefError, ValueError:
                decision = None  # fail open (spec 5.3): the default model still answers

        if decision is None:
            model = settings.default_model
            level = "off"
            reason = f"clef_unavailable fallback={model}"
            decision_id = ""
        else:
            result = decide(
                decision,
                features,
                registry.models,
                profile=options.profile or body["model"],
                default_model=settings.default_model,
                health={model_id: registry.health(model_id) for model_id in registry.models},
            )
            model = result.model
            level = result.reasoning
            reason = result.reason
            decision_id = decision.decision_id
            if model is None:
                return _error_response(
                    502, "no model available", err_type="api_error", code="no_model"
                )

        info = registry.models.get(model)
        if info is not None:
            outbound.update(reasoning_params(info, level))
        outbound["model"] = model
        headers = {
            "x-nan-auto-model": model,
            "x-nan-auto-decision-id": decision_id,
            "x-nan-auto-reason": reason,
            "x-nan-auto-fallback": "",  # the fallback chain arrives with NAU-10
        }
        content = json.dumps(outbound).encode("utf-8")
        outbound_headers = _upstream_headers(authorization)
        if bool(outbound.get("stream")):
            return await _relay_streamed(
                client, upstream_url, content, outbound_headers, extra_headers=headers
            )
        return await _relay_full(
            client, upstream_url, content, outbound_headers, extra_headers=headers
        )

    return router


async def _passthrough(
    raw: bytes,
    authorization: str,
    request: Request,
    client: httpx.AsyncClient,
    upstream_url: str,
    body: dict[str, Any] | None,
) -> Response:
    """Forward the request untouched (byte-for-byte), streaming when asked."""
    headers = {"Content-Type": request.headers.get("content-type", _JSON_MEDIA_TYPE)}
    if authorization:
        headers["Authorization"] = authorization
    if isinstance(body, dict) and bool(body.get("stream")):
        return await _relay_streamed(client, upstream_url, raw, headers)
    return await _relay_full(client, upstream_url, raw, headers)


async def _relay_full(
    client: httpx.AsyncClient,
    url: str,
    content: bytes,
    headers: dict[str, str],
    extra_headers: Mapping[str, str] | None = None,
) -> Response:
    """Forward non-streaming and reply with the upstream body as received."""
    try:
        upstream = await client.post(url, content=content, headers=headers)
    except httpx.HTTPError:
        # The exception text is not trusted: it must never reach the client.
        return _error_response(
            502, "upstream chat request failed", err_type="api_error", code="upstream_unavailable"
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", _JSON_MEDIA_TYPE),
        headers=dict(extra_headers or {}),
    )


async def _relay_streamed(
    client: httpx.AsyncClient,
    url: str,
    content: bytes,
    headers: dict[str, str],
    extra_headers: Mapping[str, str] | None = None,
) -> Response:
    """Forward streaming and relay upstream bytes incrementally, never buffered."""
    request = client.build_request("POST", url, content=content, headers=headers)
    try:
        upstream = await client.send(request, stream=True)
    except httpx.HTTPError:
        # Failure before the first byte: no status to propagate, answer our own.
        return _error_response(
            502, "upstream chat request failed", err_type="api_error", code="upstream_unavailable"
        )
    if upstream.status_code >= 400:
        # Error before the first byte: propagate status and body, close cleanly.
        body = await upstream.aread()
        await upstream.aclose()
        return Response(
            content=body,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("Content-Type", _JSON_MEDIA_TYPE),
            headers=dict(extra_headers or {}),
        )
    return StreamingResponse(
        _chunks(upstream),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", _SSE_MEDIA_TYPE),
        headers=dict(extra_headers or {}),
    )


async def _chunks(upstream: httpx.Response) -> AsyncIterator[bytes]:
    """Relay upstream bytes as they arrive; a mid-stream break propagates."""
    try:
        async for chunk in upstream.aiter_bytes():
            yield chunk
    finally:
        await upstream.aclose()


def _upstream_headers(authorization: str) -> dict[str, str]:
    """Only the client's credential and the body type travel upstream."""
    headers = {"Content-Type": _JSON_MEDIA_TYPE}
    if authorization:
        headers["Authorization"] = authorization
    return headers


def _error_response(
    status: int,
    message: str,
    err_type: str = "invalid_request_error",
    code: str | None = None,
) -> Response:
    """OpenAI-compatible error shape (constraint 7)."""
    payload = {"error": {"message": message, "type": err_type, "param": None, "code": code}}
    return Response(content=json.dumps(payload), status_code=status, media_type=_JSON_MEDIA_TYPE)


def _options_message(error: ValidationError) -> str:
    """Summarize the schema error without echoing the offending input."""
    parts = [
        "{}: {}".format(".".join(str(loc) for loc in item["loc"]), item["msg"])
        for item in error.errors()
    ]
    return "invalid nan_auto options: " + "; ".join(parts)


def _content_text(content: Any) -> str:
    """Flatten a message `content` into text, same read the features layer uses."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
        return "".join(texts)
    return ""


def _last_user_text(body: Mapping[str, Any]) -> str:
    """The last user message text, needed to bound the Clef state string."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = _content_text(message.get("content"))
            if text.strip():
                return text
    return ""


def _system_prompt(body: Mapping[str, Any]) -> str:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            return _content_text(message.get("content"))
    return ""
