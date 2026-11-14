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

Decision log (NAU-13 T6): every routed request writes one decision row — model,
reasoning level, source, timings, the Clef answers and the one upstream attempt
— through the optional `DecisionLogStore`, with the conversation entering only
as a hash. Pass-throughs write nothing (no decision was made), streaming
records only once the stream ends, and a raising store never breaks the
response: it degrades to a warning line with no content.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from hashlib import sha256
from typing import Any, Literal, cast

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import ValidationError

from nan_auto.clef_client.base import DecisionProvider
from nan_auto.clef_client.errors import ClefError
from nan_auto.clef_client.questions import build_questions, build_state
from nan_auto.clef_client.types import Decision
from nan_auto.gateway.log_store import (
    Attempt,
    ClefAnswer,
    DecisionLogStore,
    DecisionRecord,
)
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

_logger = logging.getLogger(__name__)
# The `reasoning_level` vocabulary the decision log stores (spec 9).
_REASONING_LEVELS = ("off", "low", "medium", "high")


def proxy_router(
    registry: Registry,
    provider: DecisionProvider,
    settings: Settings,
    client: httpx.AsyncClient,
    store: DecisionLogStore | None = None,
) -> APIRouter:
    """Build the chat-completions route over one registry, Clef and upstream client.

    With a `store`, every routed request additionally leaves one decision-log
    row; without one, logging stays off and the route behaves exactly as before.
    """
    router = APIRouter()
    upstream_url = f"{settings.nan_base_url}{_CHAT_COMPLETIONS_PATH}"

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        started = time.perf_counter()
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
        clef_latency_ms = 0
        last_user_text = _last_user_text(outbound)
        if last_user_text:
            clef_started = time.perf_counter()
            try:
                state = build_state(
                    last_user_text, system_prompt=_system_prompt(outbound), turns=features.turns
                )
                decision = await provider.decide(state=state, questions=build_questions())
            except ClefError, ValueError:
                decision = None  # fail open (spec 5.3): the default model still answers
            finally:
                clef_latency_ms = _elapsed_ms(clef_started)

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
        trace: _DecisionTrace | None = (
            _DecisionTrace(
                store,
                request_id=uuid.uuid4().hex,
                conversation_hash=_conversation_hash(features.conversation_key),
                profile=options.profile or body["model"],
                reasoning_level=_as_reasoning_level(level),
                decision_source="clef" if decision is not None else "fallback",
                chosen_model=model,
                clef_timeout=settings.decision_timeout_ms,
                clef_latency_ms=clef_latency_ms,
                answers=_clef_answers(decision) if decision is not None else (),
                started=started,
            )
            if store is not None
            else None
        )
        if bool(outbound.get("stream")):
            return await _relay_streamed(
                client,
                upstream_url,
                content,
                outbound_headers,
                extra_headers=headers,
                trace=trace,
            )
        response = await _relay_full(
            client, upstream_url, content, outbound_headers, extra_headers=headers, trace=trace
        )
        if trace is not None:
            trace.finish()
        return response

    return router


async def _passthrough(
    raw: bytes,
    authorization: str,
    request: Request,
    client: httpx.AsyncClient,
    upstream_url: str,
    body: dict[str, Any] | None,
) -> Response:
    """Forward the request untouched (byte-for-byte), streaming when asked.

    No decision was made, so nothing is written to the decision log.
    """
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
    trace: _DecisionTrace | None = None,
) -> Response:
    """Forward non-streaming and reply with the upstream body as received."""
    phase = time.perf_counter()
    try:
        upstream = await client.post(url, content=content, headers=headers)
    except httpx.HTTPError:
        # The exception text is not trusted: it must never reach the client.
        if trace is not None:
            trace.attempt(status=None, latency_ms=_elapsed_ms(phase))
        return _error_response(
            502, "upstream chat request failed", err_type="api_error", code="upstream_unavailable"
        )
    if trace is not None:
        trace.attempt(status=upstream.status_code, latency_ms=_elapsed_ms(phase))
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
    trace: _DecisionTrace | None = None,
) -> Response:
    """Forward streaming and relay upstream bytes incrementally, never buffered."""
    request = client.build_request("POST", url, content=content, headers=headers)
    phase = time.perf_counter()
    try:
        upstream = await client.send(request, stream=True)
    except httpx.HTTPError:
        # Failure before the first byte: no status to propagate, answer our own.
        if trace is not None:
            trace.attempt(status=None, latency_ms=_elapsed_ms(phase))
            trace.finish(delivered=False)
        return _error_response(
            502, "upstream chat request failed", err_type="api_error", code="upstream_unavailable"
        )
    if upstream.status_code >= 400:
        # Error before the first byte: propagate status and body, close cleanly.
        body = await upstream.aread()
        await upstream.aclose()
        if trace is not None:
            trace.attempt(status=upstream.status_code, latency_ms=_elapsed_ms(phase))
            trace.finish(delivered=False)
        return Response(
            content=body,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("Content-Type", _JSON_MEDIA_TYPE),
            headers=dict(extra_headers or {}),
        )
    if trace is not None:
        trace.attempt(status=upstream.status_code, latency_ms=_elapsed_ms(phase))
    return StreamingResponse(
        _chunks(upstream, trace),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", _SSE_MEDIA_TYPE),
        headers=dict(extra_headers or {}),
    )


async def _chunks(
    upstream: httpx.Response, trace: _DecisionTrace | None = None
) -> AsyncIterator[bytes]:
    """Relay upstream bytes as they arrive; a mid-stream break propagates.

    The decision write is scheduled in `finally`, so the log records once the
    stream ends — normally, broken or disconnected — and never before the first
    byte has reached the client (NAU-13 T6).
    """
    first_byte_seen = False
    delivered = False
    try:
        async for chunk in upstream.aiter_bytes():
            if trace is not None and not first_byte_seen:
                first_byte_seen = True
                trace.first_byte()
            yield chunk
        delivered = True
    finally:
        if trace is not None:
            trace.finish(delivered=delivered)
        await upstream.aclose()


def _conversation_hash(conversation_key: str) -> str:
    """The conversation enters the log only as a hash, never as raw client text."""
    return sha256(conversation_key.encode("utf-8")).hexdigest()


def _elapsed_ms(started: float) -> int:
    """Milliseconds elapsed since a `time.perf_counter()` reading."""
    return int((time.perf_counter() - started) * 1000)


def _as_reasoning_level(level: str) -> Literal["off", "low", "medium", "high"]:
    """The policy levels are exactly the log's `reasoning_level` vocabulary."""
    assert level in _REASONING_LEVELS
    return cast(Literal["off", "low", "medium", "high"], level)


def _clef_answers(decision: Decision) -> tuple[ClefAnswer, ...]:
    """The four Clef answers as typed log facts: label, value and confidence.

    `value` is the raw score for `score` questions and the probability of yes
    for `noul` ones; the label is the chosen one, from the legend when the
    question is scored.
    """
    difficulty_level = str(decision.difficulty.level())
    length_level = str(decision.expected_output_length.level())
    return (
        ClefAnswer(
            question_id="task",
            label=decision.task.label,
            value=decision.task.probabilities.get(decision.task.label, 0.0),
            confidence=decision.task.confidence,
        ),
        ClefAnswer(
            question_id="difficulty",
            label=decision.difficulty.legend.get(difficulty_level, ""),
            value=decision.difficulty.score,
            confidence=decision.difficulty.confidence,
        ),
        ClefAnswer(
            question_id="needs_reasoning",
            label="true" if decision.needs_reasoning.is_positive else "false",
            value=decision.needs_reasoning.yes,
            confidence=decision.needs_reasoning.confidence,
        ),
        ClefAnswer(
            question_id="expected_output_length",
            label=decision.expected_output_length.legend.get(length_level, ""),
            value=decision.expected_output_length.score,
            confidence=decision.expected_output_length.confidence,
        ),
    )


_PENDING_WRITES: set[asyncio.Task[None]] = set()


def _schedule_write(
    store: DecisionLogStore,
    record: DecisionRecord,
    *,
    answers: tuple[ClefAnswer, ...],
    attempts: tuple[Attempt, ...],
) -> None:
    """Fire-and-forget the decision write; it never blocks a response."""
    task = asyncio.get_running_loop().create_task(
        _write_decision(store, record, answers=answers, attempts=attempts)
    )
    _PENDING_WRITES.add(task)  # keep a reference so the task is never collected
    task.add_done_callback(_PENDING_WRITES.discard)


async def _write_decision(
    store: DecisionLogStore,
    record: DecisionRecord,
    *,
    answers: tuple[ClefAnswer, ...],
    attempts: tuple[Attempt, ...],
) -> None:
    """Write one decision, degrading to a contentless warning on failure."""
    try:
        await store.record_decision(record, answers=answers, attempts=attempts)
    except Exception:
        # Never break a response for a log failure; the line carries no content.
        _logger.warning("decision log write failed for %s", record.request_id)


class _DecisionTrace:
    """Collects one routed request's typed facts and schedules its log write.

    Only ids, enums and milliseconds ever enter the log (spec 9): the
    conversation arrives already hashed and no field carries message text or a
    credential.
    """

    def __init__(
        self,
        store: DecisionLogStore,
        *,
        request_id: str,
        conversation_hash: str,
        profile: str,
        reasoning_level: Literal["off", "low", "medium", "high"],
        decision_source: str,
        chosen_model: str,
        clef_timeout: int,
        clef_latency_ms: int,
        answers: tuple[ClefAnswer, ...],
        started: float,
    ) -> None:
        self._store = store
        self._request_id = request_id
        self._conversation_hash = conversation_hash
        self._profile = profile
        self._reasoning_level: Literal["off", "low", "medium", "high"] = reasoning_level
        self._decision_source = decision_source
        self._chosen_model = chosen_model
        self._clef_timeout = clef_timeout
        self._clef_latency_ms = clef_latency_ms
        self._answers = answers
        self._started = started
        self._attempt_status: int | None = None  # None: upstream never answered
        self._attempt_latency_ms = 0
        self._ttft_ms: int | None = None

    def attempt(self, *, status: int | None, latency_ms: int) -> None:
        """Record one upstream delivery attempt; `status` None means no answer."""
        self._attempt_status = status
        self._attempt_latency_ms = latency_ms

    def first_byte(self) -> None:
        self._ttft_ms = _elapsed_ms(self._started)

    def finish(self, *, delivered: bool = True) -> None:
        """Schedule the decision write; never blocks the caller.

        The attempt outcome uses the store's tags verbatim: `success` when the
        upstream answered below 400 and the delivery completed, `http_error`
        otherwise (the HTTP status, when one was seen, is kept as a fact).
        """
        status = self._attempt_status
        if delivered and status is not None and status < 400:
            outcome, http_status = "success", status
        else:
            outcome, http_status = "http_error", status
        _schedule_write(
            self._store,
            DecisionRecord(
                request_id=self._request_id,
                conversation_hash=self._conversation_hash,
                profile=self._profile,
                reasoning_level=self._reasoning_level,
                decision_source=self._decision_source,
                chosen_model=self._chosen_model,
                clef_timeout=self._clef_timeout,
                clef_latency_ms=self._clef_latency_ms,
                ttft_ms=self._ttft_ms,
                total_ms=_elapsed_ms(self._started),
            ),
            answers=self._answers,
            attempts=(
                Attempt(
                    model=self._chosen_model,
                    outcome=outcome,
                    http_status=http_status,
                    latency_ms=self._attempt_latency_ms,
                ),
            ),
        )


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
