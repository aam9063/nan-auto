"""The model catalog: fetch `/v1/models`, load the card, merge them.

The registry is built at startup from `GET /v1/models` (spec 4) and the local
`config/models.yaml` only adds what the API does not report. Two rules are
non-negotiable here:

- The catalog's `mode` field is never trusted to decide that a model serves
  text: anything listed under the card's `non_chat` is excluded even when the
  API announces `mode: chat` (the observed image generators), and any mode
  other than `chat` is excluded too.
- A catalog id missing from the card is kept as a generalist with sane
  defaults; it is never dropped and never given a tier it does not have.
"""

from collections.abc import Mapping
from typing import Any

import httpx
import yaml

from nan_auto.registry.errors import (
    CatalogAuthError,
    CatalogFormatError,
    CatalogUnavailable,
)
from nan_auto.registry.types import DEFAULT_CARD_PATH, CatalogEntry, ModelCard, ModelInfo

DEFAULT_URL = "https://api.nan.builders/v1/models"


# --- card loading -------------------------------------------------------------


def load_card(path: Any = DEFAULT_CARD_PATH) -> ModelCard:
    """Parse `config/models.yaml`; a malformed card is a format failure."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CatalogFormatError("model card is not a mapping")
    return ModelCard(
        models=_mapping(raw.get("models")),
        non_chat=_mapping(raw.get("non_chat")),
        tiers=_mapping(raw.get("tiers")),
    )


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# --- catalog parsing ----------------------------------------------------------


def parse_catalog(payload: object) -> tuple[CatalogEntry, ...]:
    """Map the `/v1/models` body onto catalog entries; optional fields stay optional."""
    if not isinstance(payload, dict):
        raise CatalogFormatError("catalog payload is not a JSON object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise CatalogFormatError("catalog payload has no 'data' list")
    return tuple(_parse_entry(item) for item in data)


def _parse_entry(item: object) -> CatalogEntry:
    if not isinstance(item, dict):
        raise CatalogFormatError("catalog entry is not a JSON object")
    model_id = item.get("id")
    if not isinstance(model_id, str) or not model_id:
        raise CatalogFormatError("catalog entry has no id")
    return CatalogEntry(
        id=model_id,
        # Absent mode is treated as chat: only a present non-chat mode excludes.
        mode=item.get("mode", "chat") if isinstance(item.get("mode"), str) else "chat",
        object=item["object"] if isinstance(item.get("object"), str) else "model",
        owned_by=item["owned_by"] if isinstance(item.get("owned_by"), str) else "",
        created=item["created"] if isinstance(item.get("created"), int) else 0,
        max_input_tokens=_optional_int(item.get("max_input_tokens")),
        max_output_tokens=_optional_int(item.get("max_output_tokens")),
    )


def _optional_int(value: object) -> int | None:
    # bool is an int subclass; an unusable optional field is ignored, not fatal.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


# --- merge --------------------------------------------------------------------


def merge(catalog: tuple[CatalogEntry, ...], card: ModelCard) -> dict[str, ModelInfo]:
    """Fuse the live catalog with the card into the text-chat inventory.

    Exclusion is by the local card first (`non_chat` wins over an advertised
    `chat` mode), then by any mode that is not `chat`. Card models missing
    from the catalog are appended so a degraded, card-only registry still
    serves an inventory.
    """
    tier_of = {model_id: tier for tier, model_ids in card.tiers.items() for model_id in model_ids}
    merged: dict[str, ModelInfo] = {}
    for entry in catalog:
        if entry.mode != "chat" or entry.id in card.non_chat:
            continue
        merged[entry.id] = _build(entry.id, card, tier_of, entry)
    for model_id in card.models:
        if model_id in merged or model_id in card.non_chat:
            continue
        merged[model_id] = _build(model_id, card, tier_of, None)
    return merged


def _build(
    model_id: str,
    card: ModelCard,
    tier_of: Mapping[str, str],
    entry: CatalogEntry | None,
) -> ModelInfo:
    card_entry = card.models.get(model_id, {})
    reasoning = card_entry.get("reasoning")
    reasoning = reasoning if isinstance(reasoning, dict) else {}
    context = card_entry.get("context")
    tools = card_entry.get("tools", True)
    tier = card_entry.get("tier", "")
    return ModelInfo(
        id=model_id,
        mode=entry.mode if entry is not None else "chat",
        owned_by=entry.owned_by if entry is not None else "",
        created=entry.created if entry is not None else 0,
        max_input_tokens=entry.max_input_tokens if entry is not None else None,
        max_output_tokens=entry.max_output_tokens if entry is not None else None,
        context=context if isinstance(context, str) else "",
        input=_modalities(card_entry.get("input")),
        tools=tools if isinstance(tools, bool | str) else True,
        reasoning_mode=reasoning.get("mode", "unknown"),
        reasoning_off=_reasoning_branch(reasoning, "off"),
        reasoning_on=_reasoning_branch(reasoning, "on"),
        premium=card_entry.get("premium") is True,
        excluded=card_entry.get("excluded_from_policy") is True,
        # Unknown ids get "" from the empty card entry: never a tier they lack.
        tier=tier_of.get(model_id, "") or (tier if isinstance(tier, str) else ""),
    )


_YAML_BARE_KEYS = {"off": False, "on": True}  # YAML 1.1 parses bare on/off as booleans


def _reasoning_branch(reasoning: Mapping[object, object], name: str) -> Mapping[str, object]:
    """Read the off/on branch: YAML 1.1 parses the bare keys `on`/`off` as booleans."""
    for key in (name, _YAML_BARE_KEYS[name]):
        value = reasoning.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _modalities(value: object) -> tuple[str, ...]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return ("text",)


# --- HTTP client --------------------------------------------------------------


class CatalogClient:
    """The real `/v1/models` adapter: one GET, bearer auth, explicit failures."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        url: str = DEFAULT_URL,
        timeout: float = 5.0,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._url = url
        self._timeout = timeout

    def _redact(self, text: str) -> str:
        """Strip the key from server-controlled text before it reaches a message."""
        if not self._api_key:
            return text
        return text.replace(self._api_key, "[redacted]")

    async def fetch(self) -> tuple[CatalogEntry, ...]:
        """Fetch and parse the catalog, or raise a `RegistryError`."""
        try:
            response = await self._client.get(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise CatalogUnavailable(f"catalog timeout after {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise CatalogUnavailable(f"catalog transport error: {exc}") from exc

        if response.status_code == 401:
            raise CatalogAuthError("catalog rejected the API key (401)")
        if response.status_code >= 400:
            raise CatalogUnavailable(
                f"catalog returned {response.status_code}: {self._redact(response.text)[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CatalogUnavailable("catalog returned a non-JSON body") from exc
        return parse_catalog(payload)
