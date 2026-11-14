"""Questionnaire v1 and bounded Clef state (spec 6.2, 6.3; NAU-02-T3)."""

import json

import pytest

from nan_auto.clef_client.questions import (
    QUESTION_IDS,
    build_questions,
    build_state,
    estimate_tokens,
)


def test_questionnaire_has_the_four_v1_questions() -> None:
    questions = build_questions()
    assert set(questions) == {"task", "difficulty", "needs_reasoning", "expected_output_length"}
    assert QUESTION_IDS == ("task", "difficulty", "needs_reasoning", "expected_output_length")


def test_task_is_a_choice_with_every_v1_label() -> None:
    task = build_questions()["task"]
    assert task["type"] == "choice"
    assert set(task["criteria"]) == {
        "chat_casual",
        "factual_qa",
        "coding_generate",
        "coding_debug_or_review",
        "math_or_logic",
        "summarize_or_extract",
        "translate_or_rewrite",
        "creative_writing",
        "agentic_planning",
        "other",
    }


def test_difficulty_is_a_five_point_score() -> None:
    difficulty = build_questions()["difficulty"]
    assert difficulty["type"] == "score"
    assert difficulty["criteria"] == ["Trivial", "Easy", "Medium", "Hard", "Expert"]


def test_needs_reasoning_criteria_is_a_true_false_object() -> None:
    # NaN rejects a list here with 400: "noul criteria must be an object with true and false".
    needs_reasoning = build_questions()["needs_reasoning"]
    assert needs_reasoning["type"] == "noul"
    assert set(needs_reasoning["criteria"]) == {"true", "false"}


def test_expected_output_length_is_a_four_point_score() -> None:
    length = build_questions()["expected_output_length"]
    assert length["type"] == "score"
    assert length["criteria"] == ["Very short", "Short", "Medium", "Long"]


def test_state_is_a_json_string_with_meta() -> None:
    state = build_state(last_user_message="Hola, ¿qué tal?")
    parsed = json.loads(state)
    assert isinstance(state, str)
    assert parsed["last_user_message_head"] == "Hola, ¿qué tal?"
    assert parsed["meta"]["turns"] == 1
    assert parsed["meta"]["has_code_fences"] is False


def test_state_truncates_a_huge_message_head_and_tail() -> None:
    message = "a" * 20000
    parsed = json.loads(build_state(last_user_message=message, turns=3))
    head_tokens = estimate_tokens(parsed["last_user_message_head"])
    tail_tokens = estimate_tokens(parsed["last_user_message_tail"])
    assert head_tokens <= 600
    assert tail_tokens <= 200
    assert parsed["meta"]["turns"] == 3


def test_state_records_code_fences_and_language_hint() -> None:
    parsed = json.loads(
        build_state(
            last_user_message="Mira este código:\n```py\nprint(1)\n```",
            system_prompt="You are a senior engineer.",
        )
    )
    assert parsed["meta"]["has_code_fences"] is True
    assert parsed["system_prompt_head"] == "You are a senior engineer."


def test_state_omits_empty_optional_fields() -> None:
    parsed = json.loads(build_state(last_user_message="hola"))
    assert "system_prompt_head" not in parsed


def test_state_rejects_empty_message() -> None:
    with pytest.raises(ValueError, match="last_user_message"):
        build_state(last_user_message="   ")
