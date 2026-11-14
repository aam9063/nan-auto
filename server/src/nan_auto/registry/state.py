"""In-memory per-model health and quota, plus the Registry aggregate.

Health and quota live in memory only: a failure count with a small threshold,
and an unavailable-until marker for a 402 quota exhaustion. `is_available`
combines both. Nothing here is persisted and nothing sensitive is stored.
"""

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from nan_auto.registry.catalog import merge
from nan_auto.registry.errors import RegistryError
from nan_auto.registry.types import CatalogEntry, ModelCard, ModelInfo

CatalogFetcher = Callable[[], Awaitable[tuple[CatalogEntry, ...]]]


@dataclass
class ModelHealth:
    """Mutable in-memory state for one model."""

    max_failures: int = 3
    failures: int = 0
    unavailable_until: datetime | None = None

    def mark_failure(self) -> None:
        """Record one failure of any kind (transport, 5xx, bad output)."""
        self.failures += 1

    def mark_success(self) -> None:
        """A successful call resets the failure count."""
        self.failures = 0

    def mark_quota_exhausted(self, until: datetime) -> None:
        """A 402 takes the model out until the quota is renewed (`until`)."""
        self.unavailable_until = until

    def is_available(self, now: datetime | None = None) -> bool:
        """False while the quota window lasts or the failure threshold is reached."""
        moment = now or datetime.now(UTC)
        if self.unavailable_until is not None and moment < self.unavailable_until:
            return False
        return self.failures < self.max_failures


class Registry:
    """The merged model inventory plus per-model state, refreshed with a TTL.

    The catalog comes from the injected `fetcher`; without one the registry
    works offline from the card alone (mock mode, degraded starts). A failed
    refresh never empties the registry: the last good inventory is kept, and
    the very first failure degrades to the card.
    """

    def __init__(
        self,
        card: ModelCard,
        *,
        fetcher: CatalogFetcher | None = None,
        ttl_seconds: float = 300.0,
    ) -> None:
        self._card = card
        self._fetcher = fetcher
        self._ttl = ttl_seconds
        self._models: dict[str, ModelInfo] = {}
        self._health: dict[str, ModelHealth] = {}
        self._refresh_after = 0.0  # monotonic deadline; 0.0 means stale now

    @property
    def models(self) -> Mapping[str, ModelInfo]:
        """The current merged inventory, keyed by model id."""
        return self._models

    def health(self, model_id: str) -> ModelHealth:
        """Return (creating on first use) the state of one model."""
        return self._health.setdefault(model_id, ModelHealth())

    async def refresh(self) -> None:
        """Rebuild the inventory from the catalog, tolerating a failed fetch."""
        entries: tuple[CatalogEntry, ...] = ()
        if self._fetcher is not None:
            try:
                entries = await self._fetcher()
            except RegistryError:
                if self._models:
                    self._refresh_after = time.monotonic() + self._ttl
                    return  # keep serving the last good inventory
                entries = ()  # degraded first build: card-only
        self._rebuild(entries)

    async def refresh_if_stale(self) -> None:
        """Refresh only when the TTL has elapsed; cheap on every request."""
        if time.monotonic() >= self._refresh_after:
            await self.refresh()

    def _rebuild(self, entries: tuple[CatalogEntry, ...]) -> None:
        self._models = merge(entries, self._card)
        for model_id in self._models:
            self._health.setdefault(model_id, ModelHealth())
        self._refresh_after = time.monotonic() + self._ttl
