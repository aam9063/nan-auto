"""Fail-open labeling shared by the routed and playground Clef calls.

Both `proxy.py` and `decisions.py` catch the Clef attempt and fail open
(spec 5.3); this module owns the reason string they report, so a rejected
key is never presented as Clef downtime — the dashboard shows it verbatim.
"""

from nan_auto.clef_client.errors import ClefAuthError


class ClefOutcome:
    """One Clef attempt's result: a decision, or the failure cause."""

    __slots__ = ("decision", "error")

    def __init__(self, decision: object | None, error: Exception | None) -> None:
        self.decision = decision
        self.error = error

    @property
    def fail_open_reason(self) -> str:
        """The reason string for a fail-open, naming the cause.

        A rejected key is a different incident from downtime; a ValueError
        keeps the generic `clef_unavailable` label because it names the
        caller's payload, not Clef's health.
        """
        if self.error is None:
            return "clef_unavailable"
        if isinstance(self.error, ClefAuthError):
            label = "clef_auth_error"
        elif isinstance(self.error, ValueError):
            label = "clef_unavailable"
        else:
            label = "clef_error"
        detail = str(self.error) or self.error.__class__.__name__
        return f"{label}: {detail}"
