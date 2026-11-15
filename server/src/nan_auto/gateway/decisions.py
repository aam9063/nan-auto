"""The dashboard's read API (NAU-16a): decision history, detail and playground.

Spec 8 lists what the dashboard needs to read; spec 10 shows why. Everything
here reads back typed facts only (spec 12): the store structurally holds no
prompt text and no keys, and these routes add none — the playground returns
probabilities, reason codes and the election, never the prompt it decided on.

- `GET /v1/decisions?limit=N` — recent decisions, newest first (the store's
  own order). Without a store the list is simply empty, not an error.
- `GET /v1/decisions/{id}` — one decision with its Clef answers and attempts;
  an unknown id is a 404, never a 500.
- `GET /v1/stream` — Server-Sent Events carrying each decision as the proxy
  records it. Every subscriber gets its own bounded queue: a slow client fills
  it and is dropped by the publisher instead of blocking it, and a client that
  disconnects simply closes its generator — no task is left behind.
"""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from nan_auto.clef_client.base import DecisionProvider
from nan_auto.clef_client.errors import ClefError
from nan_auto.clef_client.questions import build_questions, build_state
from nan_auto.clef_client.types import Decision
from nan_auto.gateway.clef_call import ClefOutcome
from nan_auto.gateway.events import DecisionBus, decision_payload
from nan_auto.gateway.log_store import DecisionLogEntry, DecisionLogStore
from nan_auto.gateway.proxy import _last_user_text, _system_prompt
from nan_auto.registry.state import Registry
from nan_auto.router_core.features import FeatureExtractionError, extract_features
from nan_auto.router_core.policy import decide
from nan_auto.settings import Settings

_SSE_MEDIA_TYPE = "text/event-stream"


def serialize_entry(entry: DecisionLogEntry) -> dict:
    """One stored decision as the read API and the SSE feed return it."""
    row = entry.decision
    return decision_payload(
        request_id=row.request_id,
        profile=row.profile,
        chosen_model=row.chosen_model,
        reasoning_level=row.reasoning_level,
        decision_source=row.decision_source,
        clef_timeout=row.clef_timeout,
        clef_latency_ms=row.clef_latency_ms,
        ttft_ms=row.ttft_ms,
        total_ms=row.total_ms,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        created_at=row.created_at,
        answers=entry.answers,
        attempts=entry.attempts,
    )


def decisions_router(bus: DecisionBus, store: DecisionLogStore | None = None) -> APIRouter:
    """Build the dashboard's read routes over the event bus and the log."""
    router = APIRouter()

    @router.get("/v1/decisions")
    async def list_decisions(limit: int = Query(default=50, ge=1, le=200)) -> dict:
        if store is None:
            return {"decisions": []}
        entries = await store.list_decisions(limit)
        return {"decisions": [serialize_entry(entry) for entry in entries]}

    @router.get("/v1/decisions/{request_id}")
    async def get_decision(request_id: str) -> dict:
        if store is None:
            raise HTTPException(status_code=404, detail="decision not found")
        entry = await store.get_decision(request_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="decision not found")
        return serialize_entry(entry)

    @router.get("/v1/stream")
    async def stream() -> StreamingResponse:
        sub = bus.subscribe()

        async def events() -> AsyncIterator[bytes]:
            # One bounded queue per subscriber; the bus drops a full one with a
            # `None` sentinel instead of ever blocking the publisher, and this
            # `finally` runs on disconnect too — no dangling generator or task.
            try:
                yield b": connected\n\n"
                while True:
                    event = await sub.queue.get()
                    if event is None:
                        break
                    yield event
            finally:
                bus.unsubscribe(sub)

        return StreamingResponse(events(), media_type=_SSE_MEDIA_TYPE)

    return router


class PlaygroundBody(BaseModel):
    """The playground's input: a chat-shaped body, minus the elected model.

    Only the facts the router reads are accepted; the profile defaults to the
    alias, as a routed request without `nan_auto` options would.
    """

    model: str = "nan-auto"
    messages: list[dict[str, Any]]
    profile: str | None = None
    tools: list[dict[str, Any]] | None = None


def _clef_probabilities(decision: Decision | None) -> dict:
    """The per-question probabilities spec 10's playground shows.

    Labels, scores and confidences only — the state string built from the
    prompt never comes back, and none of these fields carries message text.
    """
    if decision is None:
        return {}
    return {
        "task": {
            "label": decision.task.label,
            "probabilities": decision.task.probabilities,
            "confidence": decision.task.confidence,
        },
        "difficulty": {
            "score": decision.difficulty.score,
            "probabilities": decision.difficulty.probabilities,
            "confidence": decision.difficulty.confidence,
        },
        "needs_reasoning": {
            "yes": decision.needs_reasoning.yes,
            "confidence": decision.needs_reasoning.confidence,
        },
        "expected_output_length": {
            "score": decision.expected_output_length.score,
            "probabilities": decision.expected_output_length.probabilities,
            "confidence": decision.expected_output_length.confidence,
        },
    }


def playground_router(
    registry: Registry,
    provider: DecisionProvider,
    settings: Settings,
) -> APIRouter:
    """Build `POST /v1/playground/decide`: decide without calling any model.

    The pipeline is exactly the routed branch's decision half — features,
    Clef, the capability filter and the policy — minus the upstream relay. The
    shared upstream `httpx.AsyncClient` is never touched here, so a test can
    prove zero model calls by watching the transport.
    """
    router = APIRouter()

    @router.post("/v1/playground/decide")
    async def playground_decide(body: PlaygroundBody) -> dict:
        # The inventory is refreshed lazily, so without this the playground
        # decides against an empty catalog: nothing to reject, nothing to
        # choose, and an immediate fail-open to the default model. It must see
        # what the routed path sees.
        await registry.refresh_if_stale()
        outbound: dict[str, Any] = {"model": body.model, "messages": body.messages}
        if body.tools is not None:
            outbound["tools"] = body.tools
        try:
            features = extract_features(outbound)
        except FeatureExtractionError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        decision = None
        clef_outcome: ClefOutcome | None = None
        last_user_text = _last_user_text(outbound)
        if last_user_text:
            try:
                state = build_state(
                    last_user_text, system_prompt=_system_prompt(outbound), turns=features.turns
                )
                decision = await provider.decide(state=state, questions=build_questions())
            except (ClefError, ValueError) as clef_failure:
                decision = None  # fail open (spec 5.3): the default model still answers
                clef_outcome = ClefOutcome(None, clef_failure)

        if decision is None:
            model = settings.default_model
            level = "off"
            reason = clef_outcome.fail_open_reason if clef_outcome else "clef_unavailable"
            source = "fallback"
            candidates: tuple[str, ...] = (model,)
            rejected: dict[str, str] = {}
        else:
            result = decide(
                decision,
                features,
                registry.models,
                profile=body.profile or body.model,
                default_model=settings.default_model,
                health={model_id: registry.health(model_id) for model_id in registry.models},
            )
            if result.model is None:
                raise HTTPException(status_code=502, detail="no model available")
            model = result.model
            level = result.reasoning
            reason = result.reason
            source = "clef"
            candidates = result.candidates or (model,)
            rejected = dict(result.rejected)

        return {
            "model": model,
            "reasoning_level": level,
            "reason": reason,
            "decision_source": source,
            "candidates": list(candidates),
            "rejected": [
                {"model": model_id, "reason": code} for model_id, code in rejected.items()
            ],
            "probabilities": _clef_probabilities(decision),
        }

    return router
