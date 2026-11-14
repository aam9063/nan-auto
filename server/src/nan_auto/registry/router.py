"""The read-only registry endpoint: inventory plus state, never secrets.

The payload holds model metadata and availability only: no API key, no base
URL, no request data and no prompt text ever enter a `ModelInfo`, so the
response cannot leak them by construction.
"""

from datetime import UTC, datetime

from fastapi import APIRouter

from nan_auto.registry.state import ModelHealth, Registry
from nan_auto.registry.types import ModelInfo


def registry_router(registry: Registry) -> APIRouter:
    """Build the `/v1/registry` routes over one shared registry instance."""
    router = APIRouter()

    @router.get("/v1/registry")
    async def get_registry() -> dict:
        await registry.refresh_if_stale()
        now = datetime.now(UTC)
        data = [
            _model_payload(info, registry.health(info.id), now) for info in registry.models.values()
        ]
        return {"object": "list", "data": data}

    return router


def _model_payload(info: ModelInfo, health: ModelHealth, now: datetime) -> dict:
    return {
        "id": info.id,
        "object": "model",
        "owned_by": info.owned_by,
        "created": info.created,
        "chat": True,  # everything in the registry serves text by construction
        "mode": info.mode,
        "input": list(info.input),
        "tools": info.tools,
        "reasoning_mode": info.reasoning_mode,
        "premium": info.premium,
        "tier": info.tier,
        "max_input_tokens": info.max_input_tokens,
        "max_output_tokens": info.max_output_tokens,
        "state": {
            "available": health.is_available(now),
            "failures": health.failures,
            "quota_exhausted_until": health.unavailable_until.isoformat()
            if health.unavailable_until is not None
            else None,
        },
    }
