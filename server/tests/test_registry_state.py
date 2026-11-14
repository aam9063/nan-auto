"""Tests for per-model health/quota state and the Registry aggregate (NAU-03-T5).

The registry is exercised with an injected catalog: no client and no network
are the point here.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from test_registry_catalog import CHAT_IDS, live_catalog

from nan_auto.registry.catalog import CatalogEntry, load_card
from nan_auto.registry.errors import CatalogUnavailable
from nan_auto.registry.state import ModelHealth, Registry

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)


# --- T5: health and quota per model ------------------------------------------


def test_is_available_starts_true() -> None:
    assert ModelHealth().is_available(now=NOW)


def test_a_failure_counts_toward_the_threshold() -> None:
    health = ModelHealth(max_failures=3)
    health.mark_failure()
    assert health.is_available(now=NOW)
    assert health.failures == 1
    health.mark_failure()
    health.mark_failure()
    assert not health.is_available(now=NOW)


def test_success_resets_the_failure_count() -> None:
    health = ModelHealth(max_failures=2)
    health.mark_failure()
    health.mark_success()
    assert health.failures == 0
    assert health.is_available(now=NOW)


def test_quota_exhaustion_blocks_until_renewal() -> None:
    health = ModelHealth()
    health.mark_quota_exhausted(NOW + timedelta(hours=1))
    assert not health.is_available(now=NOW)
    # Renewal time reached: the model comes back without manual intervention.
    assert health.is_available(now=NOW + timedelta(hours=1))


def test_quota_and_failures_combine() -> None:
    health = ModelHealth(max_failures=2)
    health.mark_quota_exhausted(NOW + timedelta(hours=1))
    assert not health.is_available(now=NOW)  # quota alone is enough
    health.mark_failure()
    health.mark_failure()
    assert not health.is_available(now=NOW + timedelta(hours=2))  # failures persist


# --- Registry with an injected catalog ---------------------------------------


async def stub_fetch(payload_source: Any = live_catalog) -> tuple[CatalogEntry, ...]:
    from nan_auto.registry.catalog import parse_catalog

    return parse_catalog(payload_source())


def make_registry(**kwargs: Any) -> Registry:
    return Registry(load_card(), fetcher=stub_fetch, **kwargs)


async def test_registry_merges_the_injected_catalog() -> None:
    registry = make_registry()
    await registry.refresh()
    assert sorted(registry.models) == sorted(CHAT_IDS)
    assert registry.models["qwen3.6"].tier == "t2_generalist"


async def test_registry_keeps_unknown_generalists() -> None:
    async def fetch_with_unknown() -> tuple[CatalogEntry, ...]:
        from nan_auto.registry.catalog import parse_catalog

        payload = live_catalog()
        payload["data"].append({"id": "brand-new-model", "mode": "chat"})
        return parse_catalog(payload)

    registry = Registry(load_card(), fetcher=fetch_with_unknown)
    await registry.refresh()
    info = registry.models["brand-new-model"]
    assert info.reasoning_mode == "unknown"
    assert info.tier == ""


async def test_registry_tracks_health_per_model() -> None:
    registry = make_registry()
    await registry.refresh()
    health = registry.health("qwen3.6")
    health.mark_failure()
    registry.health("glm5.3").mark_quota_exhausted(NOW + timedelta(minutes=30))
    assert registry.health("qwen3.6").failures == 1
    assert registry.health("glm5.3").unavailable_until is not None
    assert registry.health("deepseek-v4-flash").failures == 0


async def test_health_survives_a_refresh() -> None:
    registry = make_registry()
    await registry.refresh()
    registry.health("qwen3.6").mark_failure()
    await registry.refresh()
    assert registry.health("qwen3.6").failures == 1


async def test_refresh_if_stale_skips_a_fresh_catalog() -> None:
    calls: list[int] = []

    async def counting_fetch() -> tuple[CatalogEntry, ...]:
        calls.append(1)
        return await stub_fetch()

    registry = Registry(load_card(), fetcher=counting_fetch, ttl_seconds=999.0)
    await registry.refresh_if_stale()
    await registry.refresh_if_stale()
    assert len(calls) == 1


async def test_refresh_if_stale_refetches_after_the_ttl() -> None:
    calls: list[int] = []

    async def counting_fetch() -> tuple[CatalogEntry, ...]:
        calls.append(1)
        return await stub_fetch()

    registry = Registry(load_card(), fetcher=counting_fetch, ttl_seconds=0.0)
    await registry.refresh_if_stale()
    await registry.refresh_if_stale()
    assert len(calls) == 2


async def test_failed_refresh_keeps_the_last_good_inventory() -> None:
    registry = make_registry()
    await registry.refresh()

    async def broken_fetch() -> tuple[CatalogEntry, ...]:
        raise CatalogUnavailable("catalog is down")

    registry._fetcher = broken_fetch  # noqa: SLF001 - the test stubs the transport
    await registry.refresh_if_stale()
    assert sorted(registry.models) == sorted(CHAT_IDS)


async def test_first_refresh_failure_falls_back_to_the_card() -> None:
    async def broken_fetch() -> tuple[CatalogEntry, ...]:
        raise CatalogUnavailable("catalog is down")

    registry = Registry(load_card(), fetcher=broken_fetch)
    await registry.refresh()
    # Degraded but usable: every card model that serves text is present.
    assert "qwen3.6" in registry.models
    assert "flux-2-klein" not in registry.models
    assert registry.models["glm5.3"].premium is True


async def test_registry_without_fetcher_never_touches_the_network() -> None:
    # No respx mock active: any HTTP call would raise.
    registry = Registry(load_card())
    await registry.refresh()
    assert "qwen3.6" in registry.models
