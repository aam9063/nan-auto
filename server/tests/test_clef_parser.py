"""Parser tests written against real Clef responses (spec 6, NAU-02-T2).

The expected numbers are the values captured in the fixtures, pinned as
literals on purpose: if Clef changes the wire format, this file is the alarm.
"""

import json
from pathlib import Path

import pytest

from nan_auto.clef_client.parser import ClefFormatError, parse_decision

FIXTURES = Path(__file__).parent / "fixtures" / "clef"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parses_coding_debug_fixture() -> None:
    decision = parse_decision(load("systemone_v1_coding_debug.json"))
    assert decision.model == "clef"
    assert decision.decision_id.startswith("decision-")
    assert decision.task.label == "coding_debug_or_review"
    assert decision.task.confidence == pytest.approx(0.9656098484992981)
    assert decision.difficulty.level() == 3
    assert decision.difficulty.legend["3"] == "Hard"
    assert decision.needs_reasoning.yes == pytest.approx(0.8335957527160645)
    assert decision.needs_reasoning.is_positive
    assert decision.expected_output_length.level() == 2
    assert decision.input_tokens == 518


def test_parses_casual_fixture() -> None:
    decision = parse_decision(load("systemone_v1_chat_casual_es.json"))
    assert decision.task.label == "chat_casual"
    assert decision.difficulty.level() == 1
    assert decision.needs_reasoning.is_negative


def test_parses_creative_fixture() -> None:
    decision = parse_decision(load("systemone_v1_creative.json"))
    assert decision.task.label == "creative_writing"
    assert decision.difficulty.level() == 2
    assert decision.needs_reasoning.is_negative


def test_score_probabilities_are_keyed_by_string_index_not_label() -> None:
    decision = parse_decision(load("systemone_v1_coding_debug.json"))
    assert set(decision.difficulty.probabilities) == {"0", "1", "2", "3", "4"}
    assert "Hard" not in decision.difficulty.probabilities


def test_score_confidence_is_modal_mass_not_certainty() -> None:
    # A score answer is a distribution: max mass 0.369 is the honest confidence.
    decision = parse_decision(load("systemone_v1_coding_debug.json"))
    assert decision.difficulty.confidence == pytest.approx(0.3692235052585602)
    assert decision.difficulty.modal_mass == pytest.approx(0.3692235052585602)


def test_split_distribution_is_detected_as_uncertain() -> None:
    # Fixture has substantial mass on both the Medium and Expert ends.
    decision = parse_decision(load("systemone_v1_coding_debug.json"))
    assert decision.difficulty.is_split(threshold=0.25)


def test_peaked_distribution_is_not_split() -> None:
    decision = parse_decision(load("systemone_v1_chat_casual_es.json"))
    assert not decision.difficulty.is_split(threshold=0.25)


def test_missing_answer_key_raises_format_error() -> None:
    payload = load("systemone_v1_coding_debug.json")
    del payload["answers"]["difficulty"]
    with pytest.raises(ClefFormatError, match="difficulty"):
        parse_decision(payload)


def test_error_payload_raises_format_error() -> None:
    payload = {"error": {"message": "question 'needs_reasoning': bad", "type": "BadRequestError"}}
    with pytest.raises(ClefFormatError, match="BadRequestError"):
        parse_decision(payload)


def test_boolean_score_is_rejected() -> None:
    # bool is a subclass of int: without an explicit check, "score": true parses as 1.0.
    payload = load("systemone_v1_coding_debug.json")
    payload["answers"]["difficulty"]["score"] = True
    with pytest.raises(ClefFormatError, match="not a number"):
        parse_decision(payload)


def test_boolean_noul_is_rejected() -> None:
    payload = load("systemone_v1_coding_debug.json")
    payload["answers"]["needs_reasoning"]["noul"] = False
    with pytest.raises(ClefFormatError, match="not a number"):
        parse_decision(payload)


def test_numeric_string_score_is_rejected() -> None:
    payload = load("systemone_v1_coding_debug.json")
    payload["answers"]["difficulty"]["score"] = "2.8"
    with pytest.raises(ClefFormatError, match="not a number"):
        parse_decision(payload)


def test_empty_probabilities_raise_format_error() -> None:
    # A decision with no mass is not a decision.
    payload = load("systemone_v1_coding_debug.json")
    payload["answers"]["task"]["probabilities"] = {}
    with pytest.raises(ClefFormatError, match="probabilities"):
        parse_decision(payload)


def test_list_payload_raises_format_error() -> None:
    with pytest.raises(ClefFormatError):
        parse_decision([1, 2, 3])  # type: ignore[arg-type]


def test_non_numeric_probability_mass_is_rejected() -> None:
    payload = load("systemone_v1_coding_debug.json")
    payload["answers"]["task"]["probabilities"]["other"] = "high"
    with pytest.raises(ClefFormatError, match="not a number"):
        parse_decision(payload)
