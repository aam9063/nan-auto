"""Circuit breaker and per-key concurrency limiter (NAU-10, spec 5.5).

Two pieces of state the retry walk needs and neither of which belongs to the
policy: which models are currently allowed to be tried, and how many routed
requests one client key may have in flight.

The breaker is per model and only remembers what the spec asks for: consecutive
failures open a cooldown, and a 402 (quota exhausted) keeps the model out until
the quota renews rather than for a few seconds.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _ModelState:
    """What the breaker remembers about one model."""

    failures: int = 0
    opened_until: float | None = None
    quota_exhausted: bool = False


@dataclass
class CircuitBreaker:
    """Per-model breaker: consecutive failures open a cooldown, 402 opens quota.

    `clock` is injectable so tests and callers can reason about time without
    sleeping; it defaults to the monotonic clock, which cannot jump backwards
    when the wall clock is adjusted.
    """

    threshold: int = 3
    cooldown_s: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _state: dict[str, _ModelState] = field(default_factory=dict, repr=False)

    def _entry(self, model_id: str) -> _ModelState:
        return self._state.setdefault(model_id, _ModelState())

    def record_failure(self, model_id: str) -> None:
        """Count one consecutive failure; open the cooldown at the threshold."""
        state = self._entry(model_id)
        state.failures += 1
        if state.failures >= self.threshold:
            state.opened_until = self.clock() + self.cooldown_s

    def record_success(self, model_id: str) -> None:
        """A success clears the streak, so isolated errors never accumulate."""
        state = self._entry(model_id)
        state.failures = 0
        state.opened_until = None

    def mark_quota_exhausted(self, model_id: str) -> None:
        """A 402 keeps the model out until the quota renews, not for seconds."""
        self._entry(model_id).quota_exhausted = True

    def renew_quota(self, model_id: str) -> None:
        """Reopen a model whose quota came back."""
        state = self._entry(model_id)
        state.quota_exhausted = False
        state.failures = 0
        state.opened_until = None

    def is_open(self, model_id: str) -> bool:
        """True while this model must not be tried."""
        state = self._state.get(model_id)
        if state is None:
            return False
        if state.quota_exhausted:
            return True
        if state.opened_until is None:
            return False
        if self.clock() < state.opened_until:
            return True
        # The cooldown expired: allow a probe instead of staying closed forever.
        state.opened_until = None
        state.failures = 0
        return False

    def usable(self, model_ids: Iterable[str]) -> list[str]:
        """The candidates that may be tried, in the order the policy ranked them."""
        return [model_id for model_id in model_ids if not self.is_open(model_id)]


@dataclass
class ConcurrencyLimiter:
    """Bounds in-flight routed requests per client key.

    A slot is released on every exit path, including cancellation while the
    caller is parked waiting for it, because `asyncio.Semaphore` never consumes
    a permit it did not hand out.
    """

    limit: int = 8
    _slots: dict[str, asyncio.Semaphore] = field(default_factory=dict, repr=False)

    def _semaphore(self, key: str) -> asyncio.Semaphore:
        return self._slots.setdefault(key, asyncio.Semaphore(self.limit))

    @asynccontextmanager
    async def slot(self, key: str) -> AsyncIterator[None]:
        """Hold one slot for `key` while the routed request is in flight."""
        semaphore = self._semaphore(key)
        await semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()
