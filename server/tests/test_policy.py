"""Behavior tests for the router policy (NAU-06, spec 7.2, 7.3, 6.4, 5.3).

The policy is a pure function of the Clef decision, the request features, the
inventory and the profile: every test calls it twice with the same inputs
where order matters, because stable, reproducible selection is part of the
contract.
"""

from nan_auto.clef_client.types import ChoiceAnswer, Decision, NoulAnswer, ScoreAnswer
from nan_auto.registry.types import ModelInfo
from nan_auto.router_core.features import RequestFeatures
from nan_auto.router_core.policy import PolicyResult, decide


def _info(
    model_id: str,
    *,
    input: tuple[str, ...] = ("text",),  # noqa: A002 - mirrors ModelInfo
    tools: bool | str = True,
    premium: bool = False,
    max_input_tokens: int | None = 1048576,
    tier: str = "",
) -> ModelInfo:
    """One hand-built ModelInfo shaped like the merged registry entries."""
    return ModelInfo(
        id=model_id,
        mode="chat",
        input=input,
        tools=tools,
        premium=premium,
        max_input_tokens=max_input_tokens,
        tier=tier,
    )


# The live card's chat inventory, in card order (config/models.yaml).
def _inventory(drop: tuple[str, ...] = ()) -> dict[str, ModelInfo]:
    models = {
        "gemma4": _info(
            "gemma4", input=("text", "image"), tools="xml", max_input_tokens=262144, tier="t1_fast"
        ),
        "qwen3.8-flash": _info(
            "qwen3.8-flash", input=("text", "image"), tools="xml", tier="t1_fast"
        ),
        "glm5.3-flash": _info("glm5.3-flash", input=("text", "image"), tier="t1_fast"),
        "qwen3.6": _info(
            "qwen3.6", input=("text", "image"), max_input_tokens=262144, tier="t2_generalist"
        ),
        "deepseek-v4-flash": _info("deepseek-v4-flash", tier="t2_generalist"),
        "glm5.3": _info("glm5.3", premium=True, tier="t3_max"),
        "mimo-v2.6-flash": _info(
            "mimo-v2.6-flash", input=("text", "image", "audio"), tier="t3_max"
        ),
    }
    return {model_id: info for model_id, info in models.items() if model_id not in drop}


TIER_OF = {
    "gemma4": "t1_fast",
    "qwen3.8-flash": "t1_fast",
    "glm5.3-flash": "t1_fast",
    "qwen3.6": "t2_generalist",
    "deepseek-v4-flash": "t2_generalist",
    "glm5.3": "t3_max",
    "mimo-v2.6-flash": "t3_max",
}


def _tier_of(result: PolicyResult) -> str:
    """The chosen model's tier; fails loudly when no model was elected."""
    assert result.model is not None
    return TIER_OF[result.model]


def _decision(
    task: str = "chat_casual",
    *,
    difficulty: float = 1.0,
    confidence: float = 0.95,
    needs_reasoning: bool = False,
    difficulty_probabilities: dict[str, float] | None = None,
) -> Decision:
    """A confident Clef decision, overridden per test."""
    return Decision(
        decision_id="dec-1",
        model="nan-auto",
        task=ChoiceAnswer(label=task, probabilities={task: confidence}, confidence=confidence),
        difficulty=ScoreAnswer(
            score=difficulty,
            legend={},
            probabilities=difficulty_probabilities or {"0": 1.0},
            confidence=0.95,
        ),
        needs_reasoning=NoulAnswer(yes=1.0 if needs_reasoning else 0.0, confidence=0.95),
        expected_output_length=ScoreAnswer(
            score=1.0, legend={}, probabilities={"1": 1.0}, confidence=0.95
        ),
        input_tokens=100,
    )


def _features(**overrides: object) -> RequestFeatures:
    """A plain text request, overridden per test."""
    values: dict[str, object] = {"est_tokens": 100, "response_format": "text"}
    values.update(overrides)
    return RequestFeatures(**values)  # type: ignore[arg-type]


class TestDecisionTable:
    """T5: the spec 7.2 table, row by row, under the balanced profile."""

    def test_row_chat_and_factual_easy_use_t1_with_reasoning_off(self) -> None:
        for task in ("chat_casual", "factual_qa"):
            result = decide(_decision(task, difficulty=0.0), _features(), _inventory())
            assert _tier_of(result) == "t1_fast"
            assert result.reasoning == "off"

    def test_row_chat_and_factual_medium_uses_t2_with_reasoning_off(self) -> None:
        for task in ("chat_casual", "factual_qa"):
            result = decide(_decision(task, difficulty=2.0), _features(), _inventory())
            assert _tier_of(result) == "t2_generalist"
            assert result.reasoning == "off"

    def test_row_translate_prefers_t1_and_falls_to_t2(self) -> None:
        full = decide(_decision("translate_or_rewrite", difficulty=1.0), _features(), _inventory())
        without_t1 = decide(
            _decision("translate_or_rewrite", difficulty=1.0),
            _features(),
            _inventory(drop=("gemma4", "qwen3.8-flash", "glm5.3-flash")),
        )

        assert _tier_of(full) == "t1_fast"
        assert _tier_of(without_t1) == "t2_generalist"
        assert full.reasoning == "off" and without_t1.reasoning == "off"

    def test_row_summarize_uses_long_context_models_only_with_long_context(self) -> None:
        long_input = decide(
            _decision("summarize_or_extract", difficulty=1.0),
            _features(est_tokens=150000),
            _inventory(),
        )
        short_input = decide(
            _decision("summarize_or_extract", difficulty=1.0), _features(), _inventory()
        )

        assert long_input.model == "deepseek-v4-flash"
        assert _tier_of(short_input) == "t1_fast"
        assert long_input.reasoning == "off" and short_input.reasoning == "off"

    def test_row_creative_writing_uses_t2_with_reasoning_off(self) -> None:
        result = decide(_decision("creative_writing", difficulty=3.0), _features(), _inventory())

        assert _tier_of(result) == "t2_generalist"
        assert result.reasoning == "off"

    def test_row_coding_generation_easy_uses_t2_reasoning_off_then_low(self) -> None:
        easy = decide(_decision("coding_generate", difficulty=1.0), _features(), _inventory())
        medium = decide(_decision("coding_generate", difficulty=2.0), _features(), _inventory())

        assert _tier_of(easy) == "t2_generalist" and easy.reasoning == "off"
        assert _tier_of(medium) == "t2_generalist" and medium.reasoning == "low"

    def test_row_coding_generation_hard_prefers_glm53_when_premium_allows(self) -> None:
        balanced = decide(_decision("coding_generate", difficulty=3.0), _features(), _inventory())
        fast = decide(
            _decision("coding_generate", difficulty=3.0),
            _features(),
            _inventory(),
            profile="nan-auto-fast",
        )

        assert balanced.model == "glm5.3" and balanced.reasoning == "high"
        assert fast.model == "deepseek-v4-flash"

    def test_row_debug_and_review_uses_t2_or_t3_with_reasoning_on(self) -> None:
        for difficulty in (2.0, 3.0, 4.0):
            result = decide(
                _decision("coding_debug_or_review", difficulty=difficulty),
                _features(),
                _inventory(),
            )

            assert _tier_of(result) in ("t2_generalist", "t3_max")
            assert result.reasoning == "high"

    def test_row_math_and_logic_uses_t2_or_t3_with_reasoning_on(self) -> None:
        for difficulty in (2.0, 3.0, 4.0):
            result = decide(
                _decision("math_or_logic", difficulty=difficulty), _features(), _inventory()
            )

            assert _tier_of(result) in ("t2_generalist", "t3_max")
            assert result.reasoning == "high"

    def test_row_agentic_or_tools_uses_t2_or_t3_per_needs_reasoning(self) -> None:
        agentic = decide(
            _decision("agentic_planning", difficulty=1.0, needs_reasoning=True),
            _features(),
            _inventory(),
        )
        with_tools = decide(
            _decision("chat_casual", difficulty=1.0, needs_reasoning=False),
            _features(has_tools=True),
            _inventory(),
        )

        assert _tier_of(agentic) in ("t2_generalist", "t3_max")
        assert agentic.reasoning == "high"
        assert _tier_of(with_tools) in ("t2_generalist", "t3_max")
        assert with_tools.reasoning == "off"

    def test_unknown_task_falls_back_to_the_generalist_row(self) -> None:
        result = decide(_decision("some_future_task", difficulty=1.0), _features(), _inventory())

        assert _tier_of(result) in ("t2_generalist", "t3_max")


class TestWhenInDoubtTierUp:
    """T6: spec 6.4 — low confidence or split difficulty raises exactly one tier."""

    def test_low_task_confidence_raises_exactly_one_tier(self) -> None:
        confident = decide(
            _decision("chat_casual", difficulty=0.0, confidence=0.90),
            _features(),
            _inventory(),
        )
        unsure = decide(
            _decision("chat_casual", difficulty=0.0, confidence=0.40),
            _features(),
            _inventory(),
        )

        assert _tier_of(confident) == "t1_fast"
        assert _tier_of(unsure) == "t2_generalist"

    def test_split_difficulty_raises_exactly_one_tier(self) -> None:
        decided = _decision(
            "math_or_logic",
            difficulty=3.0,
            difficulty_probabilities={"1": 0.4, "3": 0.2, "4": 0.4},
        )
        result = decide(decided, _features(), _inventory())

        assert _tier_of(result) == "t3_max"

    def test_decision_is_reproducible_under_uncertainty(self) -> None:
        decided = _decision("chat_casual", difficulty=0.0, confidence=0.40)

        first = decide(decided, _features(), _inventory())
        second = decide(decided, _features(), _inventory())

        assert first == second


class TestScoringSelectionAndFailOpen:
    """T7: stable scoring, fact-only reason, open fallback (spec 5.3)."""

    def test_candidate_order_is_stable_and_reproducible(self) -> None:
        decided = _decision("coding_debug_or_review", difficulty=3.0)
        features = _features()

        first = decide(decided, features, _inventory())
        second = decide(decided, features, _inventory())

        assert first == second
        assert first.model == first.candidates[0]
        assert first.candidates == (
            "qwen3.6",
            "deepseek-v4-flash",
            "glm5.3",
            "mimo-v2.6-flash",
            "gemma4",
            "qwen3.8-flash",
            "glm5.3-flash",
        )

    def test_quota_pressure_pushes_a_failing_model_down_the_order(self) -> None:
        from nan_auto.registry.state import ModelHealth

        health = {"gemma4": ModelHealth(max_failures=3, failures=2)}
        result = decide(
            _decision("chat_casual", difficulty=0.0), _features(), _inventory(), health=health
        )

        # Same fit, so the failing model lands after both healthy t1 siblings.
        assert result.candidates.index("gemma4") > result.candidates.index("qwen3.8-flash")
        assert result.candidates.index("gemma4") > result.candidates.index("glm5.3-flash")

    def test_reason_string_carries_only_facts(self) -> None:
        result = decide(
            _decision("coding_debug_or_review", difficulty=3.0), _features(), _inventory()
        )

        assert result.reason == "coding_debug_or_review d=3 reasoning=on"

    def test_fail_open_returns_the_profile_default_when_no_candidate_survives(self) -> None:
        result = decide(
            _decision("coding_generate", difficulty=3.0),
            _features(),
            _inventory(drop=tuple(TIER_OF)),
            default_model="deepseek-v4-flash",
        )

        assert result.model == "deepseek-v4-flash"
        assert result.fallback is True
        assert result.reason == "coding_generate d=3 reasoning=on fallback=deepseek-v4-flash"

    def test_fail_open_without_a_default_carries_no_model_and_does_not_raise(self) -> None:
        result = decide(
            _decision("chat_casual", difficulty=0.0),
            _features(),
            _inventory(drop=tuple(TIER_OF)),
        )

        assert result.model is None
        assert result.fallback is True
        assert result.candidates == ()


def _trivial_decision() -> Decision:
    return Decision(
        decision_id="d",
        model="clef",
        task=ChoiceAnswer(
            label="chat_casual",
            probabilities={"chat_casual": 0.9, "other": 0.1},
            confidence=0.9,
        ),
        difficulty=ScoreAnswer(
            score=1.0,
            legend={"0": "Trivial", "1": "Easy", "2": "Medium", "3": "Hard", "4": "Expert"},
            probabilities={"1": 0.8, "0": 0.1, "2": 0.1},
            confidence=0.8,
        ),
        needs_reasoning=NoulAnswer(yes=0.1, confidence=0.9),
        expected_output_length=ScoreAnswer(
            score=0.0,
            legend={"0": "Very short", "1": "Short", "2": "Medium", "3": "Long"},
            probabilities={"0": 0.9},
            confidence=0.9,
        ),
    )


def test_locked_reasoning_loses_to_a_model_that_can_switch_it_off() -> None:
    """When the policy wants reasoning off, an always-on model is not equal.

    Two models in the same tier with the same fit differ only in whether the
    switch exists: asking for `off` on a model that ignores it buys no latency,
    which is the whole point of turning reasoning off.
    """
    locked = ModelInfo(
        id="glm5.3-flash", mode="chat", input=("text",), tier="t1_fast", reasoning_mode="always_on"
    )
    switchable = ModelInfo(
        id="gemma4",
        mode="chat",
        input=("text",),
        tier="t1_fast",
        reasoning_mode="togglable_default_off",
    )
    result = decide(
        _trivial_decision(),
        RequestFeatures(has_image=False, has_audio=False, est_tokens=10, turns=1),
        {"glm5.3-flash": locked, "gemma4": switchable},
        profile="nan-auto",
    )
    assert result.reasoning == "off"
    assert result.model == "gemma4"
