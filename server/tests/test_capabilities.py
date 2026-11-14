"""Behavior tests for the capability filter (NAU-06, spec 7).

The filter is a pure function of request features, inventory, profile and
difficulty: every test below calls it twice with the same inputs and expects
the same result, because reproducibility is part of the contract.
"""

from datetime import UTC, datetime, timedelta

from nan_auto.registry.state import ModelHealth
from nan_auto.registry.types import ModelCard, ModelInfo
from nan_auto.router_core.capabilities import filter_candidates
from nan_auto.router_core.features import RequestFeatures


def _info(
    model_id: str,
    *,
    input: tuple[str, ...] = ("text",),  # noqa: A002 - mirrors ModelInfo
    tools: bool | str = True,
    premium: bool = False,
    excluded: bool = False,
    max_input_tokens: int | None = None,
    tier: str = "",
) -> ModelInfo:
    """One hand-built ModelInfo shaped like the merged registry entries."""
    return ModelInfo(
        id=model_id,
        mode="chat",
        input=input,
        tools=tools,
        premium=premium,
        excluded=excluded,
        max_input_tokens=max_input_tokens,
        tier=tier,
    )


def _features(**overrides: object) -> RequestFeatures:
    """A plain text request, overridden per test."""
    values: dict[str, object] = {"est_tokens": 100, "response_format": "text"}
    values.update(overrides)
    return RequestFeatures(**values)  # type: ignore[arg-type]


class TestPremiumAndPolicyExclusion:
    """T4: premium follows the profile, `excluded` never passes, health is respected."""

    _INVENTORY = {
        "glm5.3": _info("glm5.3", premium=True),
        "qwen3.6": _info("qwen3.6"),
    }

    def test_fast_profile_never_allows_premium(self) -> None:
        result = filter_candidates(
            _features(), self._INVENTORY, profile="nan-auto-fast", difficulty=4
        )

        assert result.candidates == ("qwen3.6",)
        assert result.rejected["glm5.3"] == "premium_not_allowed"

    def test_balanced_profile_allows_premium_only_from_difficulty_three(self) -> None:
        easy = filter_candidates(_features(), self._INVENTORY, difficulty=2)
        hard = filter_candidates(_features(), self._INVENTORY, difficulty=3)

        assert easy.candidates == ("qwen3.6",)
        assert easy.rejected["glm5.3"] == "premium_not_allowed"
        assert hard.candidates == ("glm5.3", "qwen3.6")
        assert hard.rejected == {}

    def test_quality_profile_allows_premium_at_any_difficulty(self) -> None:
        result = filter_candidates(
            _features(), self._INVENTORY, profile="nan-auto-quality", difficulty=0
        )

        assert result.candidates == ("glm5.3", "qwen3.6")
        assert result.rejected == {}

    def test_excluded_models_are_never_candidates(self) -> None:
        inventory = {
            "minimax-h3": _info("minimax-h3", premium=True, excluded=True),
            "qwen3.6": _info("qwen3.6"),
        }

        result = filter_candidates(_features(), inventory, profile="nan-auto-quality", difficulty=4)

        assert result.candidates == ("qwen3.6",)
        assert result.rejected["minimax-h3"] == "excluded_from_policy"

    def test_models_past_the_failure_threshold_are_unavailable(self) -> None:
        health = {
            "qwen3.6": ModelHealth(max_failures=3, failures=3),
            "glm5.3-flash": ModelHealth(max_failures=3, failures=2),
        }
        inventory = {
            "glm5.3-flash": _info("glm5.3-flash"),
            "qwen3.6": _info("qwen3.6"),
        }

        result = filter_candidates(_features(), inventory, health=health)

        assert result.candidates == ("glm5.3-flash",)
        assert result.rejected["qwen3.6"] == "unavailable"

    def test_models_in_a_quota_window_are_unavailable_until_it_ends(self) -> None:
        now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
        health = {"qwen3.6": ModelHealth(unavailable_until=now + timedelta(minutes=10), failures=0)}
        inventory = {
            "glm5.3-flash": _info("glm5.3-flash"),
            "qwen3.6": _info("qwen3.6"),
        }

        result = filter_candidates(_features(), inventory, health=health, now=now)

        assert result.candidates == ("glm5.3-flash",)
        assert result.rejected["qwen3.6"] == "unavailable"

    def test_three_profiles_give_different_candidates_on_the_same_inventory(self) -> None:
        fast = filter_candidates(
            _features(), self._INVENTORY, profile="nan-auto-fast", difficulty=2
        )
        balanced = filter_candidates(_features(), self._INVENTORY, difficulty=2)
        quality = filter_candidates(
            _features(), self._INVENTORY, profile="nan-auto-quality", difficulty=2
        )

        assert fast.candidates == ("qwen3.6",)
        assert balanced.candidates == ("qwen3.6",)
        assert quality.candidates == ("glm5.3", "qwen3.6")

    def test_unknown_profile_falls_back_to_balanced(self) -> None:
        result = filter_candidates(
            _features(), self._INVENTORY, profile="no-such-profile", difficulty=2
        )

        assert result.candidates == ("qwen3.6",)
        assert result.rejected["glm5.3"] == "premium_not_allowed"


class TestToolsAndFormatFilter:
    """T3: XML tool calling is penalized; unsupported json_schema drops the model."""

    def test_tool_request_penalizes_xml_tool_models(self) -> None:
        inventory = {
            "gemma4": _info("gemma4", tools="xml"),
            "glm5.3-flash": _info("glm5.3-flash", tools=True),
        }

        result = filter_candidates(_features(has_tools=True), inventory)

        assert result.candidates == ("glm5.3-flash",)
        assert result.penalized == ("gemma4",)
        assert result.rejected == {"gemma4": "xml_tools"}

    def test_xml_tool_models_survive_only_when_nothing_else_does(self) -> None:
        inventory = {
            "gemma4": _info("gemma4", tools="xml"),
            "qwen3.8-flash": _info("qwen3.8-flash", tools="xml"),
        }

        result = filter_candidates(_features(has_tools=True), inventory)

        assert result.candidates == ("gemma4", "qwen3.8-flash")
        assert result.penalized == ("gemma4", "qwen3.8-flash")
        assert result.rejected == {}

    def test_toolless_request_does_not_penalize_xml_models(self) -> None:
        inventory = {"gemma4": _info("gemma4", tools="xml")}

        result = filter_candidates(_features(), inventory)

        assert result.candidates == ("gemma4",)
        assert result.penalized == ()

    def test_json_schema_request_drops_models_the_card_marks_unsupported(self) -> None:
        card = ModelCard(
            models={
                "deepseek-v4-flash": {
                    "notes": (
                        "json_schema unsupported, use json_object with the schema in the prompt"
                    )
                }
            }
        )
        inventory = {
            "deepseek-v4-flash": _info("deepseek-v4-flash"),
            "qwen3.6": _info("qwen3.6"),
        }

        result = filter_candidates(_features(response_format="json_schema"), inventory, card=card)

        assert result.candidates == ("qwen3.6",)
        assert result.rejected == {"deepseek-v4-flash": "no_json_schema"}

    def test_json_object_format_does_not_drop_card_marked_models(self) -> None:
        card = ModelCard(
            models={
                "deepseek-v4-flash": {
                    "notes": (
                        "json_schema unsupported, use json_object with the schema in the prompt"
                    )
                }
            }
        )
        inventory = {"deepseek-v4-flash": _info("deepseek-v4-flash")}

        result = filter_candidates(_features(response_format="json_object"), inventory, card=card)

        assert result.candidates == ("deepseek-v4-flash",)
        assert result.rejected == {}


class TestContextFilter:
    """T2: `est_tokens` above `max_input_tokens` disqualifies the model."""

    def test_oversized_prompt_drops_the_small_context_model(self) -> None:
        inventory = {
            "gemma4": _info("gemma4", max_input_tokens=262144),
            "glm5.3-flash": _info("glm5.3-flash", max_input_tokens=1048576),
        }

        result = filter_candidates(_features(est_tokens=300000), inventory)

        assert result.candidates == ("glm5.3-flash",)
        assert result.rejected == {"gemma4": "context_too_small"}

    def test_tokens_equal_to_the_limit_are_still_accepted(self) -> None:
        inventory = {"gemma4": _info("gemma4", max_input_tokens=262144)}

        result = filter_candidates(_features(est_tokens=262144), inventory)

        assert result.candidates == ("gemma4",)
        assert result.rejected == {}

    def test_model_without_a_reported_limit_is_never_dropped_for_context(self) -> None:
        inventory = {"mystery-model": _info("mystery-model", max_input_tokens=None)}

        result = filter_candidates(_features(est_tokens=10_000_000), inventory)

        assert result.candidates == ("mystery-model",)
        assert result.rejected == {}


class TestModalityFilter:
    """T1: image and audio only go to models that accept them."""

    def test_image_request_drops_text_only_models_with_reason(self) -> None:
        inventory = {
            "glm5.3": _info("glm5.3"),
            "mimo-v2.6-flash": _info("mimo-v2.6-flash", input=("text", "image", "audio")),
        }

        result = filter_candidates(_features(has_image=True), inventory)

        assert result.candidates == ("mimo-v2.6-flash",)
        assert result.rejected == {"glm5.3": "no_image_support"}

    def test_audio_request_keeps_only_audio_capable_models(self) -> None:
        inventory = {
            "glm5.3": _info("glm5.3"),
            "mimo-v2.6-flash": _info("mimo-v2.6-flash", input=("text", "image", "audio")),
        }

        result = filter_candidates(_features(has_audio=True), inventory)

        assert result.candidates == ("mimo-v2.6-flash",)
        assert result.rejected == {"glm5.3": "no_audio_support"}

    def test_text_request_keeps_every_model(self) -> None:
        inventory = {
            "glm5.3": _info("glm5.3"),
            "mimo-v2.6-flash": _info("mimo-v2.6-flash", input=("text", "image", "audio")),
        }

        result = filter_candidates(_features(), inventory)

        assert result.candidates == ("glm5.3", "mimo-v2.6-flash")
        assert result.rejected == {}

    def test_filter_is_reproducible(self) -> None:
        inventory = {
            "glm5.3": _info("glm5.3"),
            "mimo-v2.6-flash": _info("mimo-v2.6-flash", input=("text", "image", "audio")),
        }

        first = filter_candidates(_features(has_image=True), inventory)
        second = filter_candidates(_features(has_image=True), inventory)

        assert first == second
