"""Parse Clef `structured_decision` payloads into normalized decisions.

The response schema is not documented by NaN. This parser is written against
captured real responses in `server/tests/fixtures/clef/`; see the verified
contract in `odd/tasks/nau-02-decision-provider.md`.
"""

from typing import Any

from nan_auto.clef_client.types import (
    ChoiceAnswer,
    Decision,
    NoulAnswer,
    ScoreAnswer,
)


class ClefFormatError(RuntimeError):
    """The payload does not match the contract the router depends on."""


def parse_decision(payload: dict[str, Any]) -> Decision:
    """Map a Clef response body onto a normalized :class:`Decision`."""
    if "error" in payload:
        error = payload["error"]
        kind = error.get("type", "unknown") if isinstance(error, dict) else "unknown"
        message = error.get("message", "") if isinstance(error, dict) else str(error)
        raise ClefFormatError(f"Clef error {kind}: {message}")

    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise ClefFormatError("payload has no 'answers' object")

    return Decision(
        decision_id=str(payload.get("id", "")),
        model=str(payload.get("model", "")),
        task=_parse_choice(_answer(answers, "task")),
        difficulty=_parse_score(_answer(answers, "difficulty")),
        needs_reasoning=_parse_noul(_answer(answers, "needs_reasoning")),
        expected_output_length=_parse_score(_answer(answers, "expected_output_length")),
        input_tokens=_input_tokens(payload),
    )


def _answer(answers: dict[str, Any], question_id: str) -> dict[str, Any]:
    value = answers.get(question_id)
    if not isinstance(value, dict):
        raise ClefFormatError(f"missing answer for question '{question_id}'")
    return value


def _probabilities(raw: Any, question_id: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ClefFormatError(f"'{question_id}' has no probabilities object")
    return {str(key): float(mass) for key, mass in raw.items()}


def _parse_choice(answer: dict[str, Any]) -> ChoiceAnswer:
    label = answer.get("choice")
    if not isinstance(label, str) or not label:
        raise ClefFormatError("'task' answer has no choice label")
    probabilities = _probabilities(answer.get("probabilities"), "task")
    confidence = float(answer.get("confidence", probabilities.get(label, 0.0)))
    return ChoiceAnswer(label=label, probabilities=probabilities, confidence=confidence)


def _parse_score(answer: dict[str, Any]) -> ScoreAnswer:
    raw_score = answer.get("score")
    if not isinstance(raw_score, int | float):
        raise ClefFormatError("score answer has no numeric 'score'")
    legend = answer.get("legend")
    if not isinstance(legend, dict):
        raise ClefFormatError("score answer has no 'legend'")
    probabilities = _probabilities(answer.get("probabilities"), "score")
    confidence = float(answer.get("confidence", max(probabilities.values(), default=0.0)))
    return ScoreAnswer(
        score=float(raw_score),
        legend={str(key): str(label) for key, label in legend.items()},
        probabilities=probabilities,
        confidence=confidence,
    )


def _parse_noul(answer: dict[str, Any]) -> NoulAnswer:
    raw = answer.get("noul")
    if not isinstance(raw, int | float):
        raise ClefFormatError("'needs_reasoning' answer has no numeric 'noul'")
    return NoulAnswer(yes=float(raw), confidence=float(answer.get("confidence", raw)))


def _input_tokens(payload: dict[str, Any]) -> int:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get("input_tokens", 0)
    return int(value) if isinstance(value, int | float) else 0
