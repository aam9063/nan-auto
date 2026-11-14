"""The `/v1/chat/completions` gateway route (NAU-07 proxy, NAU-08 streaming).

One route, two behaviors (spec 5, 8, 12 and 14):

- An alias model (`nan-auto`, `nan-auto-fast`, `nan-auto-quality`) triggers the
  routing path: validate `nan_auto`, strip it, extract features, ask Clef,
  elect a model with the policy, merge the reasoning parameters and forward a
  rewritten body with the real model id. Four `x-nan-auto-*` headers annotate
  every routed reply, streaming or not.
- Any other model is a byte-for-byte pass-through: no decision, no header, no
  body rewrite.

Relay rules (NAU-10): a routed request walks the policy's candidate chain —
the elected model first — skipping candidates the circuit breaker holds open,
with at most `fallback_max_retries` retries after the first attempt. The
model switches only before the first byte reaches the client; once the stream
has started a break propagates and is never retried. A 429 honours
`Retry-After` before the next attempt, a 402 marks the model's quota
exhausted on the breaker, and exhausting the chain propagates the last real
upstream status and body — never a generic 500. One limiter slot per client
key bounds the in-flight routed requests. Without `stream` the upstream body
is forwarded as received; with `stream` bytes are relayed as they arrive,
never buffered.

Secret hygiene: the client's `Authorization` header is forwarded to the chat
upstream and touches nothing else — it is never stored, logged, echoed back or
embedded in an error message.

Decision log (NAU-13 T6): every routed request writes one decision row — model,
reasoning level, source, timings, the Clef answers and every upstream attempt
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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
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
from nan_auto.gateway.fallback import CircuitBreaker, ConcurrencyLimiter
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
    row (NAU-13 T6); without it, decision logging stays off.
    """
    router = APIRouter()
    upstream_url = f"{settings.nan_base_url}{_CHAT_COMPLETIONS_PATH}"
    breaker = CircuitBreaker(
        threshold=settings.breaker_threshold, cooldown_s=settings.breaker_cooldown_s
    )
    limiter = ConcurrencyLimiter(limit=settings.key_concurrency_limit)

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
            # Fail open (spec 5.3): only the default model can answer.
            chain: tuple[str, ...] = (settings.default_model,)
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
            chain = result.candidates or (model,)

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

        # One limiter slot per client key covers the whole walk; on the
        # streaming-success path the relay generator takes the release over,
        # so the slot spans the stream too. Every other exit path releases
        # here, cancellation included (`ConcurrencyLimiter.slot` never
        # consumes a permit it did not hand out).
        slot_cm = limiter.slot(authorization)
        await slot_cm.__aenter__()
        handed_off = False  # True once the streaming relay owns the release
        released = False

        async def release() -> None:
            """Return the slot exactly once; every delivery path ends in one."""
            nonlocal released
            if released:
                return
            released = True
            await slot_cm.__aexit__(None, None, None)

        try:
            tried: list[str] = []
            attempts_used = 0
            last_real: tuple[int, bytes, str] | None = None  # status, body, media type
            transport_failed_last = False
            handoff: tuple[httpx.Response, str] | None = None
            final: Response | None = None
            is_stream = bool(outbound.get("stream"))
            max_attempts = 1 + max(settings.fallback_max_retries, 0)

            def routed_headers() -> dict[str, str]:
                """The `x-nan-auto-*` annotations for the delivery as it ended.

                `x-nan-auto-fallback` lists every model tried before the final
                one, in policy order, and is present but empty otherwise.
                """
                return {
                    "x-nan-auto-model": tried[-1],
                    "x-nan-auto-decision-id": decision_id,
                    "x-nan-auto-reason": reason,
                    "x-nan-auto-fallback": ",".join(tried[:-1]),
                }

            while attempts_used < max_attempts and final is None and handoff is None:
                # The breaker decides who may be tried; the walk itself never
                # visits a candidate twice, so a 402 model is never retried.
                candidate = next(
                    (
                        model_id
                        for model_id in chain
                        if model_id not in tried and not breaker.is_open(model_id)
                    ),  # noqa: E501
                    None,
                )
                if candidate is None:
                    break
                attempts_used += 1
                tried.append(candidate)
                payload = dict(outbound)
                info = registry.models.get(candidate)
                if info is not None:
                    payload.update(reasoning_params(info, level))
                payload["model"] = candidate
                attempt_phase = time.perf_counter()
                received: httpx.Response | None = None
                is_streaming = False
                try:
                    if is_stream:
                        request_attempt = client.build_request(
                            "POST",
                            upstream_url,
                            content=json.dumps(payload).encode("utf-8"),
                            headers=outbound_headers,
                        )
                        received = await client.send(request_attempt, stream=True)
                        is_streaming = True
                    else:
                        received = await client.post(
                            upstream_url,
                            content=json.dumps(payload).encode("utf-8"),
                            headers=outbound_headers,
                        )
                except httpx.HTTPError:
                    # The exception text is not trusted: it never reaches the client.
                    received = None
                retry_after = 0.0
                if received is None:
                    # Transport failure before any byte: a failed attempt, and
                    # if the walk ends here there is no status to propagate.
                    breaker.record_failure(candidate)
                    transport_failed_last = True
                    if trace is not None:
                        trace.attempt(
                            model=candidate, status=None, latency_ms=_elapsed_ms(attempt_phase)
                        )
                else:
                    status = received.status_code
                    if trace is not None:
                        trace.attempt(
                            model=candidate, status=status, latency_ms=_elapsed_ms(attempt_phase)
                        )
                    if status < 400:
                        breaker.record_success(candidate)
                        media_type = received.headers.get(
                            "Content-Type", _SSE_MEDIA_TYPE if is_streaming else _JSON_MEDIA_TYPE
                        )
                        if is_streaming:
                            # Bytes about to flow: past this point the model
                            # can no longer be switched (spec 5.5).
                            handoff = (received, media_type)
                        else:
                            final = Response(
                                content=received.content,
                                status_code=status,
                                media_type=media_type,
                                headers=routed_headers(),
                            )
                    elif status < 500 and status not in (402, 429):
                        # A client error no candidate can fix: the real one
                        # propagates, untouched by the walk.
                        body, media_type = await _buffer_body(received, is_streaming)
                        final = Response(
                            content=body,
                            status_code=status,
                            media_type=media_type,
                            headers=routed_headers(),
                        )
                    else:
                        if status == 402:
                            breaker.mark_quota_exhausted(candidate)
                        else:
                            breaker.record_failure(candidate)
                        body, media_type = await _buffer_body(received, is_streaming)
                        last_real = (status, body, media_type)
                        transport_failed_last = False
                        retry_after = _retry_after_seconds(received.headers.get("Retry-After"))
                if final is not None or handoff is not None:
                    break
                # Honour an announced `Retry-After` before the next attempt —
                # but wait only when another attempt can actually follow.
                more = any(
                    model_id not in tried and not breaker.is_open(model_id) for model_id in chain
                )
                if retry_after > 0.0 and more and attempts_used < max_attempts:
                    await asyncio.sleep(retry_after)

            if handoff is not None:
                stream_upstream, media_type = handoff
                handed_off = True  # the relay generator releases the slot
                return StreamingResponse(
                    _chunks(stream_upstream, trace, on_done=release),
                    status_code=stream_upstream.status_code,
                    media_type=media_type,
                    headers=routed_headers(),
                )
            if final is not None:
                if trace is not None:
                    await trace.finish()
                return final
            if not tried:
                # Every candidate is out (breaker open, quota gone): there is
                # no upstream status of this request left to propagate.
                return _error_response(
                    502, "no model available", err_type="api_error", code="no_model"
                )
            # The delivery plan failed as a whole: every candidate still in it
            # shares this request's failure, so the next walk starts clean of
            # the models this chain implicated (spec 5.5).
            # Only attempted models earn a failure. Marking the candidates the
            # budget never reached would blame a healthy model for someone
            # else's outage and quietly degrade routing during the cooldown.
            if trace is not None:
                await trace.finish(delivered=False)
            if transport_failed_last or last_real is None:
                return _error_response(
                    502,
                    "upstream chat request failed",
                    err_type="api_error",
                    code="upstream_unavailable",
                )
            exhaust_status, exhaust_body, exhaust_media_type = last_real
            return Response(
                content=exhaust_body,
                status_code=exhaust_status,
                media_type=exhaust_media_type,
                headers=routed_headers(),
            )
        finally:
            if not handed_off:
                await release()

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
) -> Response:
    """Forward a pass-through non-streaming: the upstream body as received.

    Requests without a router alias take this path untouched — the fallback
    walk belongs to the routed branch only. No decision was made, so nothing
    is written to the decision log.
    """
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
    )


async def _relay_streamed(
    client: httpx.AsyncClient,
    url: str,
    content: bytes,
    headers: dict[str, str],
) -> Response:
    """Forward a pass-through streaming request, relayed incrementally.

    The routed branch walks candidates before relaying; a pass-through request
    goes upstream exactly once, and a mid-stream break propagates.
    """
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
        )
    return StreamingResponse(
        _chunks(upstream),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", _SSE_MEDIA_TYPE),
    )


async def _buffer_body(response: httpx.Response, streaming: bool) -> tuple[bytes, str]:
    """Read an upstream response the walk will not relay, and close it."""
    if streaming:
        body = await response.aread()
        await response.aclose()
    else:
        body = response.content
    return body, response.headers.get("Content-Type", _JSON_MEDIA_TYPE)


def _retry_after_seconds(value: str | None) -> float:
    """A `Retry-After` header as seconds; an unusable value waits zero."""
    if not value:
        return 0.0
    try:
        return max(float(value), 0.0)
    except ValueError:
        return 0.0


async def _chunks(
    upstream: httpx.Response,
    trace: _DecisionTrace | None = None,
    on_done: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[bytes]:
    """Relay upstream bytes as they arrive; a mid-stream break propagates.

    The decision write is awaited in `finally`, so the log records once the
    stream ends — normally, broken or disconnected — and never before the first
    byte has reached the client (NAU-13 T6). `on_done`, when given, runs in the
    same `finally`: it releases the limiter slot the walk handed over, on every
    path including cancellation.
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
            await trace.finish(delivered=delivered)
        await upstream.aclose()
        if on_done is not None:
            await on_done()


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
    """Collects one routed request's typed facts and writes its log row.

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
        self._attempts: list[tuple[str, int | None, int]] = []
        self._ttft_ms: int | None = None

    def attempt(self, *, model: str, status: int | None, latency_ms: int) -> None:
        """Record one upstream attempt; `status` None means no answer arrived."""
        self._attempts.append((model, status, latency_ms))

    def first_byte(self) -> None:
        self._ttft_ms = _elapsed_ms(self._started)

    async def finish(self, *, delivered: bool = True) -> None:
        """Write the decision row; the caller awaits it at the delivery's end.

        Every attempt in the walk gets a row. Earlier attempts always ended in
        a retryable error, so each one logs `http_error` with the HTTP status
        it saw (None for a transport failure). The last attempt's outcome uses
        the store's tags verbatim: `success` when the upstream answered below
        400 and the delivery completed, `http_error` otherwise (the HTTP
        status, when one was seen, is kept as a fact). The request's chosen
        model is the one the delivery ended on.
        """
        attempts: list[Attempt] = [
            Attempt(model=model, outcome="http_error", http_status=status, latency_ms=latency_ms)
            for model, status, latency_ms in self._attempts[:-1]
        ]
        chosen = self._chosen_model
        if self._attempts:
            last_model, last_status, last_latency = self._attempts[-1]
            chosen = last_model
            if delivered and last_status is not None and last_status < 400:
                outcome, http_status = "success", last_status
            else:
                outcome, http_status = "http_error", last_status
            attempts.append(
                Attempt(
                    model=last_model,
                    outcome=outcome,
                    http_status=http_status,
                    latency_ms=last_latency,
                )
            )
        await _write_decision(
            self._store,
            DecisionRecord(
                request_id=self._request_id,
                conversation_hash=self._conversation_hash,
                profile=self._profile,
                reasoning_level=self._reasoning_level,
                decision_source=self._decision_source,
                chosen_model=chosen,
                clef_timeout=self._clef_timeout,
                clef_latency_ms=self._clef_latency_ms,
                ttft_ms=self._ttft_ms,
                total_ms=_elapsed_ms(self._started),
            ),
            answers=self._answers,
            attempts=tuple(attempts),
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
