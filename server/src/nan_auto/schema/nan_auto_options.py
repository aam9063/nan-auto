"""Schema of the optional `nan_auto` field on the chat request (spec 12).

The gateway validates clients keep their hands inside the contract: a body may
carry one `nan_auto` object with a known profile and boolean hints. Anything
else is a schema error, answered before any network call is made.

The field is stripped from the outbound body on the routing path; only
pass-through requests (non-alias model) forward it untouched.
"""

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

from nan_auto.router_core.capabilities import PROFILES


class NanAutoOptions(BaseModel):
    """Validated client hints carried in the optional `nan_auto` body field."""

    model_config = ConfigDict(extra="forbid")

    # When set, overrides the profile implied by the model alias.
    profile: str | None = None
    # Boolean hints; surfaced to the decision layer in later tasks. Strict:
    # a `"true"` string is a schema error, not a silent coercion.
    explain: StrictBool = False
    allow_premium: StrictBool = False

    @field_validator("profile")
    @classmethod
    def known_profile(cls, value: str | None) -> str | None:
        """Reject an unknown profile name instead of silently re-routing it."""
        if value is None or value in PROFILES:
            return value
        allowed = ", ".join(sorted(PROFILES))
        raise ValueError(f"profile must be one of: {allowed}")

    @property
    def effective_profile(self) -> str | None:
        """The profile override to apply, if any."""
        return self.profile
