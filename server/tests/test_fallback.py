"""NAU-10 unit tests: the per-model circuit breaker and the per-key limiter.

The breaker counts consecutive failures per model and opens a cooldown; a 402
(quota exhausted) takes the model out until quota renewal, not seconds. The
limiter bounds in-flight routed requests per client key and must release the
slot on every path, including cancellation.
"""

import asyncio
import contextlib

from nan_auto.gateway.fallback import CircuitBreaker, ConcurrencyLimiter
from nan_auto.settings import Settings

# --- circuit breaker ----------------------------------------------------------


def test_the_breaker_opens_after_threshold_consecutive_failures() -> None:
    breaker = CircuitBreaker(threshold=3, cooldown_s=60.0)
    breaker.record_failure("m")
    breaker.record_failure("m")
    assert not breaker.is_open("m")  # below threshold: still usable
    breaker.record_failure("m")
    assert breaker.is_open("m")
    assert not breaker.is_open("other")  # per model, not global


def test_a_success_resets_the_consecutive_failure_count() -> None:
    breaker = CircuitBreaker(threshold=3, cooldown_s=60.0)
    breaker.record_failure("m")
    breaker.record_failure("m")
    breaker.record_success("m")
    breaker.record_failure("m")
    assert not breaker.is_open("m")  # one failure after a success is not 3


async def test_the_cooldown_reopens_the_model_when_it_expires() -> None:
    breaker = CircuitBreaker(threshold=1, cooldown_s=0.05)
    breaker.record_failure("m")
    assert breaker.is_open("m")
    await asyncio.sleep(0.06)
    assert not breaker.is_open("m")  # seconds, not forever


async def test_a_402_keeps_the_model_out_until_quota_renewal_not_seconds() -> None:
    breaker = CircuitBreaker(threshold=1, cooldown_s=0.01)
    breaker.mark_quota_exhausted("m")
    await asyncio.sleep(0.05)  # far past any cooldown
    assert breaker.is_open("m")
    breaker.renew_quota("m")
    assert not breaker.is_open("m")


def test_usable_keeps_the_policy_order_and_drops_open_models() -> None:
    breaker = CircuitBreaker(threshold=1, cooldown_s=60.0)
    breaker.record_failure("b")
    assert breaker.usable(("a", "b", "c")) == ["a", "c"]
    assert breaker.usable(()) == []


# --- concurrency limiter ------------------------------------------------------


async def test_the_limiter_serializes_slots_beyond_the_limit() -> None:
    limiter = ConcurrencyLimiter(limit=1)
    active = 0
    peak = 0

    async def work() -> None:
        nonlocal active, peak
        async with limiter.slot("key"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(work(), work(), work())
    assert peak == 1  # the limit held for the whole burst


async def test_cancellation_releases_the_slot() -> None:
    limiter = ConcurrencyLimiter(limit=1)

    async def hold() -> None:
        async with limiter.slot("key"):
            await asyncio.sleep(30)

    async with limiter.slot("key"):
        waiter = asyncio.create_task(hold())
        await asyncio.sleep(0.01)
        assert not waiter.done()  # it is parked waiting for the slot
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter
    # The cancelled waiter released the slot: this must not deadlock.
    async with asyncio.timeout(1.0):
        async with limiter.slot("key"):
            pass


# --- settings -----------------------------------------------------------------


def test_settings_expose_the_nau10_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # no env file: assert pure code defaults
    settings = Settings()
    assert settings.breaker_threshold == 3
    assert settings.breaker_cooldown_s == 60.0
    assert settings.fallback_max_retries == 2  # spec 5.3: at most two retries
    assert settings.key_concurrency_limit == 8


async def test_a_quota_exhaustion_expires_so_a_long_lived_process_recovers() -> None:
    """NaN publishes no quota endpoint, so time is the only renewal signal.

    Without an expiry a single 402 removes the model until the process
    restarts, which for a long-lived demo host means forever.
    """
    breaker = CircuitBreaker(threshold=1, cooldown_s=60.0, quota_cooldown_s=0.05)
    breaker.mark_quota_exhausted("m")
    assert breaker.is_open("m")
    await asyncio.sleep(0.06)
    assert not breaker.is_open("m")
