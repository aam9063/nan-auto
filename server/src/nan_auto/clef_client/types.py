"""Normalized decision types shared by every DecisionProvider.

The shapes here are the router's vocabulary, not Clef's wire format: the
`nan` adapter parses the real payload into these types (see parser.py), so a
change in Clef's undocumented JSON stays inside one module.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChoiceAnswer:
    """A `choice` question: one label with its probability mass."""

    label: str
    probabilities: dict[str, float]
    confidence: float


@dataclass(frozen=True)
class ScoreAnswer:
    """A `score` question: a position on an ordinal scale.

    Clef keys `probabilities` by the stringified index and sends the labels
    separately in `legend`, so this type keeps both.
    """

    score: float
    legend: dict[str, str]
    probabilities: dict[str, float]
    confidence: float

    def level(self) -> int:
        """Return the scale position the score rounds to."""
        return round(self.score)

    @property
    def modal_mass(self) -> float:
        """Probability mass on the most likely level."""
        return max(self.probabilities.values(), default=0.0)

    def is_split(self, threshold: float = 0.25) -> bool:
        """True when mass sits on both ends of the scale, so the read is ambiguous."""
        levels = [int(key) for key, mass in self.probabilities.items() if mass >= threshold]
        if len(levels) < 2:
            return False
        return max(levels) - min(levels) >= 2


@dataclass(frozen=True)
class NoulAnswer:
    """A `noul` (yes/no) question: a single probability of yes."""

    yes: float
    confidence: float

    @property
    def is_positive(self) -> bool:
        return self.yes >= 0.5

    @property
    def is_negative(self) -> bool:
        return not self.is_positive


@dataclass(frozen=True)
class Decision:
    """One Clef decision, normalized."""

    decision_id: str
    model: str
    task: ChoiceAnswer
    difficulty: ScoreAnswer
    needs_reasoning: NoulAnswer
    expected_output_length: ScoreAnswer
    input_tokens: int = 0
