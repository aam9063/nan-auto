"""Offline decision provider: keyword rules, no network.

Lets the router be developed, tested in CI and rehearsed with
`CLEF_PROVIDER=mock` without a key or connectivity (spec 13).
"""

import json
import re

from nan_auto.clef_client.types import (
    ChoiceAnswer,
    Decision,
    NoulAnswer,
    ScoreAnswer,
)

_DIFFICULTY_LEGEND = {"0": "Trivial", "1": "Easy", "2": "Medium", "3": "Hard", "4": "Expert"}
_LENGTH_LEGEND = {"0": "Very short", "1": "Short", "2": "Medium", "3": "Long"}

_CODING = re.compile(
    r"\b(code|bug|stack ?trace|traceback|error|python|typescript|function|refactor|test)\b", re.I
)
_DEBUG = re.compile(r"\b(debug|fix|why does|traceback|stack ?trace|fails?|broken)\b", re.I)
_MATH = re.compile(r"\b(solve|prove|integral|derivative|equation|probability|theorem)\b", re.I)
_TRANSLATE = re.compile(r"\b(translate|traduce|rewrite|rephrase|resume|summarize)\b", re.I)
_CREATIVE = re.compile(r"\b(story|poem|cuento|poema|slogan|tagline|write a)\b", re.I)


class MockDecisionProvider:
    """`DecisionProvider` that classifies with regular expressions."""

    async def decide(self, *, state: str, questions: dict) -> Decision:
        """Return a deterministic decision derived from the bounded state."""
        text = self._extract_text(state)
        task = self._classify(text)
        difficulty = self._difficulty(task, text)
        needs_reasoning = (
            task
            in {
                "coding_debug_or_review",
                "math_or_logic",
                "agentic_planning",
                "coding_generate",
            }
            or len(text) > 800
        )
        return Decision(
            decision_id="decision-mock",
            model="mock",
            task=ChoiceAnswer(
                label=task,
                probabilities={task: 0.8, "other": 0.2},
                confidence=0.8,
            ),
            difficulty=difficulty,
            needs_reasoning=NoulAnswer(yes=0.85 if needs_reasoning else 0.15, confidence=0.85),
            expected_output_length=ScoreAnswer(
                score=2.0 if len(text) > 400 else 1.0,
                legend=dict(_LENGTH_LEGEND),
                probabilities={"1": 0.5, "2": 0.4, "0": 0.1},
                confidence=0.5,
            ),
            input_tokens=max(1, len(text) // 4),
        )

    @staticmethod
    def _extract_text(state: str) -> str:
        try:
            parsed = json.loads(state)
        except json.JSONDecodeError:
            return state
        if isinstance(parsed, dict):
            head = parsed.get("last_user_message_head", "")
            return head if isinstance(head, str) else str(head)
        return state

    @staticmethod
    def _classify(text: str) -> str:
        if _DEBUG.search(text) and _CODING.search(text):
            return "coding_debug_or_review"
        if _MATH.search(text):
            return "math_or_logic"
        if _TRANSLATE.search(text):
            return "summarize_or_extract"
        if _CREATIVE.search(text):
            return "creative_writing"
        if _CODING.search(text):
            return "coding_generate"
        if len(text) < 120:
            return "chat_casual"
        return "factual_qa"

    @staticmethod
    def _difficulty(task: str, text: str) -> ScoreAnswer:
        base = {
            "chat_casual": 0.5,
            "factual_qa": 1.4,
            "summarize_or_extract": 1.5,
            "creative_writing": 1.7,
            "translate_or_rewrite": 1.2,
            "coding_generate": 2.2,
            "coding_debug_or_review": 2.9,
            "math_or_logic": 3.0,
            "agentic_planning": 3.0,
            "other": 2.0,
        }.get(task, 2.0)
        bump = 0.5 if len(text) > 600 else 0.0
        return ScoreAnswer(
            score=min(4.0, base + bump),
            legend=dict(_DIFFICULTY_LEGEND),
            probabilities={"0": 0.05, "1": 0.15, "2": 0.4, "3": 0.3, "4": 0.1},
            confidence=0.4,
        )
