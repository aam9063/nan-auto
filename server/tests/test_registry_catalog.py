"""Contract tests for the registry catalog: client, card merge and exclusions.

NAU-03-T1..T4. The catalog shape is the one observed on the live API on
2026-11-14: 18 models, of which the two image generators are advertised with
`mode: chat`. No network except through respx.
"""

import httpx
import pytest
import respx

from nan_auto.registry.catalog import CatalogClient, load_card, merge, parse_catalog
from nan_auto.registry.errors import (
    CatalogAuthError,
    CatalogFormatError,
    CatalogUnavailable,
    RegistryError,
)
from nan_auto.registry.types import CatalogEntry

URL = "https://api.nan.builders/v1/models"

CHAT_IDS = [
    "qwen3.6",
    "deepseek-v4-flash",
    "glm5.3",
    "mimo-v2.6-flash",
    "qwen3.8-flash",
    "glm5.3-flash",
    "gemma4",
    "minimax-h3",
    "qwen3.8-max",
    "kimi-k3.1-pro",
    "deepseek-v4-pro",
    "hy4",
]
# The last two are announced with mode "chat" although they generate images.
NON_CHAT_IDS = [
    "qwen3-embedding",
    "rerank",
    "kokoro",
    "whisper",
    "flux-2-klein",
    "qwen-image-2.1",
]
CATALOG_IDS = CHAT_IDS + NON_CHAT_IDS


def live_catalog() -> dict:
    """A catalog with the exact shape observed on 2026-11-14."""
    modes = {
        **{model_id: "chat" for model_id in CHAT_IDS},
        "qwen3-embedding": "embedding",
        "rerank": "rerank",
        "kokoro": "audio_speech",
        "whisper": "audio_transcription",
        "flux-2-klein": "chat",
        "qwen-image-2.1": "chat",
    }
    data = []
    for model_id in CATALOG_IDS:
        entry: dict = {
            "id": model_id,
            "created": 1731561600,
            "object": "model",
            "owned_by": "nan",
            "mode": modes[model_id],
        }
        if model_id in ("qwen3.8-flash", "glm5.3-flash", "glm5.3", "qwen3.8-max", "hy4"):
            entry["max_input_tokens"] = 1000000
        if model_id == "qwen3.8-max":
            entry["max_output_tokens"] = 131072
        data.append(entry)
    return {"object": "list", "data": data}


# --- T1: parsing the /v1/models payload --------------------------------------


def test_parse_returns_all_18_models() -> None:
    entries = parse_catalog(live_catalog())
    assert [entry.id for entry in entries] == CATALOG_IDS
    assert len(entries) == 18


def test_parse_tolerates_missing_optional_fields() -> None:
    payload = {"object": "list", "data": [{"id": "solo", "mode": "chat"}]}
    (entry,) = parse_catalog(payload)
    assert entry.id == "solo"
    assert entry.mode == "chat"
    assert entry.max_input_tokens is None
    assert entry.max_output_tokens is None


@pytest.mark.parametrize(
    "payload",
    [
        [1, 2, 3],  # not an object
        {"object": "list"},  # no data list
        {"object": "list", "data": [42]},  # entry is not an object
        {"object": "list", "data": [{"mode": "chat"}]},  # no id
        {"object": "list", "data": [{"id": "", "mode": "chat"}]},  # empty id
    ],
)
def test_parse_rejects_malformed_payloads(payload: object) -> None:
    with pytest.raises(CatalogFormatError):
        parse_catalog(payload)  # type: ignore[arg-type]


def client(api_key: str = "sk-test") -> CatalogClient:
    return CatalogClient(client=httpx.AsyncClient(), api_key=api_key)


@respx.mock
async def test_catalog_client_parses_the_200_body() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=live_catalog()))
    entries = await client().fetch()
    assert [entry.id for entry in entries] == CATALOG_IDS


@respx.mock
async def test_catalog_client_sends_the_bearer_token() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=live_catalog()))
    await client(api_key="sk-test").fetch()
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test"


@respx.mock
async def test_catalog_client_401_raises_auth_error_without_retry() -> None:
    respx.get(URL).mock(return_value=httpx.Response(401, json={"error": {}}))
    with pytest.raises(CatalogAuthError):
        await client().fetch()


@respx.mock
async def test_catalog_client_500_raises_unavailable() -> None:
    respx.get(URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(CatalogUnavailable):
        await client().fetch()


@respx.mock
async def test_catalog_client_timeout_raises_unavailable() -> None:
    respx.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(CatalogUnavailable, match="timeout"):
        await client().fetch()


@respx.mock
async def test_catalog_client_non_json_body_raises_unavailable() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(CatalogUnavailable, match="non-JSON"):
        await client().fetch()


@respx.mock
async def test_catalog_client_wrong_shape_raises_format_error() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    with pytest.raises(CatalogFormatError):
        await client().fetch()


@respx.mock
async def test_catalog_client_never_leaks_the_key_in_error_messages() -> None:
    canary = "sk-CANARY-9911"
    cases = {
        "timeout": httpx.ReadTimeout("slow"),
        "401": httpx.Response(401, json={"error": {"message": "invalid key"}}),
        "403-echoing-headers": httpx.Response(403, text=f"denied for authorization={canary}"),
        "500": httpx.Response(500, text="boom"),
        "non-json": httpx.Response(200, text="<html>maintenance</html>"),
    }
    for name, side_effect in cases.items():
        respx.get(URL).mock(side_effect=side_effect)
        with pytest.raises(RegistryError) as exc_info:  # 401 raises the auth subclass
            await client(api_key=canary).fetch()
        assert canary not in str(exc_info.value), f"canary leaked in the {name} path"


# --- T2: merging with config/models.yaml -------------------------------------


def merged_live() -> dict:
    return merge(parse_catalog(live_catalog()), load_card())


def test_card_model_keeps_its_metadata() -> None:
    info = merged_live()["qwen3.6"]
    assert info.input == ("text", "image")
    assert info.tools is True
    assert info.reasoning_mode == "togglable"
    assert info.premium is False
    assert info.tier == "t2_generalist"


def test_premium_and_tier_come_from_the_card() -> None:
    glm = merged_live()["glm5.3"]
    assert glm.premium is True
    assert glm.tier == "t3_max"


def test_reasoning_variants_survive_the_merge() -> None:
    gemma = merged_live()["gemma4"]
    assert gemma.reasoning_mode == "togglable_default_off"
    assert gemma.reasoning_on == {"chat_template_kwargs": {"enable_thinking": True}}
    deepseek = merged_live()["deepseek-v4-flash"]
    assert deepseek.reasoning_mode == "effort"


def test_api_limits_are_kept_when_present() -> None:
    assert merged_live()["qwen3.8-max"].max_input_tokens == 1000000
    assert merged_live()["qwen3.8-max"].max_output_tokens == 131072
    assert merged_live()["qwen3.6"].max_input_tokens is None


# --- T3: unknown id becomes a generalist -------------------------------------


def catalog_with_unknown() -> dict:
    payload = live_catalog()
    payload["data"].append({"id": "brand-new-model", "mode": "chat", "owned_by": "nan"})
    return payload


def test_unknown_id_is_present_as_generalist() -> None:
    merged = merge(parse_catalog(catalog_with_unknown()), load_card())
    info = merged["brand-new-model"]
    assert info.input == ("text",)
    assert info.tools is True
    assert info.reasoning_mode == "unknown"
    assert info.premium is False


def test_unknown_id_never_gets_a_tier() -> None:
    merged = merge(parse_catalog(catalog_with_unknown()), load_card())
    assert merged["brand-new-model"].tier == ""


# --- T4: non-chat exclusion by card, never by trusting mode ------------------


def test_image_generators_advertised_as_chat_are_excluded() -> None:
    # flux-2-klein and qwen-image-2.1 arrive with mode "chat": only the local
    # card can keep them out of the text registry.
    merged = merged_live()
    assert "flux-2-klein" not in merged
    assert "qwen-image-2.1" not in merged


def test_non_chat_modes_are_excluded() -> None:
    merged = merged_live()
    for model_id in ("qwen3-embedding", "rerank", "kokoro", "whisper"):
        assert model_id not in merged


def test_every_chat_model_survives() -> None:
    merged = merged_live()
    assert sorted(merged) == sorted(CHAT_IDS)


def test_card_can_exclude_a_model_from_the_policy_without_dropping_it() -> None:
    """`excluded_from_policy` in the card flags a model; the inventory keeps it."""
    card = load_card()
    catalog = (CatalogEntry(id="minimax-h3", mode="chat"),)
    infos = merge(catalog, card)
    assert "minimax-h3" in infos
    assert infos["minimax-h3"].excluded is True
    assert infos["minimax-h3"].premium is True
