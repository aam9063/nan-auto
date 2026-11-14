"""The single interface every decision provider implements (spec 5.2)."""

from typing import Protocol

from nan_auto.clef_client.types import Decision


class DecisionProvider(Protocol):
    """Turns a bounded state plus questionnaire into a normalized decision."""

    async def decide(self, *, state: str, questions: dict) -> Decision:
        """Return a decision, or raise when this provider cannot decide."""
        ...
