"""In-process event bus and payload shaping for the decision read API (NAU-16a).

The bus is the one channel the proxy publishes each recorded decision to and
`GET /v1/stream` subscribes to. Payload shaping lives here too, so the read
routes and the SSE events return exactly the same facts — and only those
facts: ids, enums, numbers and timings, never prompt text or a key (spec 12).
"""

import asyncio
import contextlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["DecisionBus", "Subscription", "decision_payload"]

# Per-subscriber queue bound: a slow client fills it and is dropped instead of
# blocking the publisher (spec 8's feed requirement).
_QUEUE_LIMIT = 64


def decision_payload(
    *,
    request_id: str,
    profile: str,
    chosen_model: str | None,
    reasoning_level: str,
    decision_source: str,
    clef_timeout: int,
    clef_latency_ms: int,
    ttft_ms: int | None,
    total_ms: int | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    created_at: str | None,
    answers: Iterable[Any],
    attempts: Iterable[Any],
) -> dict:
    """One decision as the dashboard may see it — typed facts only.

    `answers` and `attempts` are the store's frozen dataclasses (`ClefAnswer`,
    `Attempt`). The shapes mirror the store's rows (spec 9): the Clef answers
    keep label, value and confidence; the attempts keep model, outcome tag,
    HTTP status and latency. No field carries conversation text, a response
    body or a credential.
    """
    return {
        "id": request_id,
        "profile": profile,
        "chosen_model": chosen_model,
        "reasoning_level": reasoning_level,
        "decision_source": decision_source,
        "clef_timeout_ms": clef_timeout,
        "clef_latency_ms": clef_latency_ms,
        "ttft_ms": ttft_ms,
        "total_ms": total_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "created_at": created_at,
        "answers": [
            {
                "question_id": answer.question_id,
                "label": answer.label,
                "value": answer.value,
                "confidence": answer.confidence,
            }
            for answer in answers
        ],
        "attempts": [
            {
                "model": attempt.model,
                "outcome": attempt.outcome,
                "http_status": attempt.http_status,
                "latency_ms": attempt.latency_ms,
            }
            for attempt in attempts
        ],
    }


@dataclass(eq=False)
class Subscription:
    """One SSE subscriber: a bounded queue plus a liveness flag.

    `active` turns False when the bus drops this subscriber (its queue filled)
    or the consumer left; the sentinel `None` enqueued on drop closes the
    consumer's loop without it ever blocking the publisher.
    """

    queue: asyncio.Queue[bytes | None] = field(default_factory=lambda: asyncio.Queue(_QUEUE_LIMIT))
    active: bool = True


class DecisionBus:
    """Publish decisions to every live subscriber, never blocking.

    `publish` is synchronous and non-awaiting: a slow subscriber's bounded
    queue fills, the subscriber is dropped (and its consumer closed with a
    sentinel), and the publisher moves on to the rest.
    """

    def __init__(self, queue_limit: int = _QUEUE_LIMIT) -> None:
        self._limit = queue_limit
        self._subs: set[Subscription] = set()

    def subscribe(self) -> Subscription:
        """Register a new subscriber with its own bounded queue."""
        sub = Subscription(queue=asyncio.Queue(self._limit))
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        """Remove a subscriber; safe to call more than once."""
        sub.active = False
        self._subs.discard(sub)

    def publish(self, payload: dict) -> None:
        """Deliver one serialized event to every subscriber; drop the full ones.

        A subscriber that cannot keep up is dropped, not blocked behind: the
        publisher never awaits, and the dropped consumer receives the `None`
        sentinel and ends.
        """
        event = b"event: decision\ndata: " + json.dumps(payload).encode("utf-8") + b"\n\n"
        for sub in list(self._subs):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                self.unsubscribe(sub)
                # Make room for the sentinel the consumer waits on.
                with contextlib.suppress(asyncio.QueueEmpty):
                    sub.queue.get_nowait()
                sub.queue.put_nowait(None)
