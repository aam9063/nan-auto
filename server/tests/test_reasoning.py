"""Behavior tests for the per-model reasoning adapter (NAU-09, spec 7.4).

The adapter is a pure function of the merged `ModelInfo` and the abstract
reasoning level the policy already decided (NAU-06): it returns the exact
fields to merge into the forwarded body, or nothing for models without a
documented switch. Every test builds its `ModelInfo` by hand in the shape
`registry.catalog.merge` produces — no YAML is parsed here, so the YAML 1.1
bare `on`/`off` trap never reaches this module.
"""

from typing import cast

import pytest

from nan_auto.registry.types import ModelInfo
from nan_auto.router_core.reasoning import reasoning_params


def _info(
    model_id: str,
    *,
    mode: str = "unknown",
    off: dict[str, object] | None = None,
    on: dict[str, object] | None = None,
) -> ModelInfo:
    """One hand-built ModelInfo shaped like the merged registry entries."""
    return ModelInfo(
        id=model_id,
        mode="chat",
        reasoning_mode=mode,
        reasoning_off=off or {},
        reasoning_on=on or {},
    )


# Real card values from config/models.yaml, as the merge would deliver them.
QWEN36 = dict(
    mode="togglable",
    off={"chat_template_kwargs": {"enable_thinking": False}},
    on={"chat_template_kwargs": {"enable_thinking": True}},
)
DEEPSEEK = dict(mode="effort")
GEMMA4 = dict(mode="togglable_default_off", on={"chat_template_kwargs": {"enable_thinking": True}})

# always_on models from the card, each with its own test (NAU-09-T4).
ALWAYS_ON_IDS = ("mimo-v2.6-flash", "glm5.3-flash", "glm5.3", "qwen3.8-flash")


class TestTogglable:
    """NAU-09-T1: qwen3.6 translates off/on to chat_template_kwargs."""

    def test_off_disables_thinking(self) -> None:
        info = _info("qwen3.6", **QWEN36)  # type: ignore[arg-type]

        assert reasoning_params(info, "off") == {"chat_template_kwargs": {"enable_thinking": False}}

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_any_on_level_enables_thinking(self, level: str) -> None:
        info = _info("qwen3.6", **QWEN36)  # type: ignore[arg-type]

        assert reasoning_params(info, level) == {"chat_template_kwargs": {"enable_thinking": True}}


class TestEffort:
    """NAU-09-T2: deepseek-v4-flash maps the level onto reasoning_effort."""

    @pytest.mark.parametrize(
        ("level", "value"), [("low", "low"), ("medium", "medium"), ("high", "high")]
    )
    def test_on_levels_send_reasoning_effort(self, level: str, value: str) -> None:
        info = _info("deepseek-v4-flash", **DEEPSEEK)  # type: ignore[arg-type]

        assert reasoning_params(info, level) == {"reasoning_effort": value}

    def test_off_sends_nothing(self) -> None:
        info = _info("deepseek-v4-flash", **DEEPSEEK)  # type: ignore[arg-type]

        assert reasoning_params(info, "off") == {}


class TestTogglableDefaultOff:
    """NAU-09-T3: gemma4 treats `low` as off; medium and high switch on."""

    @pytest.mark.parametrize("level", ["off", "low"])
    def test_off_and_low_count_as_off(self, level: str) -> None:
        info = _info("gemma4", **GEMMA4)  # type: ignore[arg-type]

        assert reasoning_params(info, level) == {}

    @pytest.mark.parametrize("level", ["medium", "high"])
    def test_medium_and_high_enable_thinking(self, level: str) -> None:
        info = _info("gemma4", **GEMMA4)  # type: ignore[arg-type]

        assert reasoning_params(info, level) == {"chat_template_kwargs": {"enable_thinking": True}}


class TestAlwaysOn:
    """NAU-09-T4: always-on models get no fields, whatever the level."""

    @pytest.mark.parametrize("model_id", ALWAYS_ON_IDS)
    @pytest.mark.parametrize("level", ["off", "low", "medium", "high"])
    def test_no_fields_at_any_level(self, model_id: str, level: str) -> None:
        info = _info(model_id, mode="always_on")

        assert reasoning_params(info, level) == {}


class TestUnknown:
    """NAU-09-T5: no card entry or unknown level means no fields, no raise."""

    def test_unknown_model_sends_nothing(self) -> None:
        # An id missing from the card merges with reasoning_mode `unknown`
        # and empty branches: no invented parameters.
        info = _info("brand-new-model")

        assert reasoning_params(info, "high") == {}

    @pytest.mark.parametrize("level", ["", "maximum", "ON", None])
    def test_unknown_level_falls_back_to_off(self, level: object) -> None:
        info = _info("qwen3.6", **QWEN36)  # type: ignore[arg-type]

        assert reasoning_params(info, cast(str, level)) == reasoning_params(info, "off")


class TestPurity:
    """The result is safe to `body.update(...)`: fresh dicts, no card refs."""

    def test_merging_never_mutates_the_inventory(self) -> None:
        info = _info("qwen3.6", **QWEN36)  # type: ignore[arg-type]
        card_on = info.reasoning_on

        result = cast(dict[str, object], reasoning_params(info, "high"))
        result["chat_template_kwargs"]["enable_thinking"] = "corrupted"  # type: ignore[index]
        result["top_level"] = 1

        assert card_on["chat_template_kwargs"]["enable_thinking"] is True  # type: ignore[index]
        assert "top_level" not in card_on
        assert reasoning_params(info, "high") == {"chat_template_kwargs": {"enable_thinking": True}}
