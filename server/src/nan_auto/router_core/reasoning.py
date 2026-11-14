"""Per-model reasoning adapter (NAU-09, spec 5.2 and 7.4).

NAU-06 (policy) decides the abstract level — `off`, `low`, `medium`, `high` —
but every model turns reasoning on and off in its own way. This module is the
pure translation layer between the two: given the merged `ModelInfo` and the
level, it returns the exact fields to merge into the body forwarded to NaN.
No network, no clock, no mutation of the input; a model without a documented
switch receives no fields even if the policy asked for `high`.

The field names and payloads come from the card (`reasoning.mode`,
`reasoning.off`, `reasoning.on`) via `ModelInfo`, never from hardcoded model
ids. The caller merges the result with `body.update(...)`; returned dicts are
fresh copies, so mutating them cannot corrupt the inventory.
"""

from collections.abc import Mapping

from nan_auto.registry.types import ModelInfo

_OFF = "off"
# Levels that mean "reasoning on", in ascending effort order.
_LEVELS = ("low", "medium", "high")


def reasoning_params(info: ModelInfo, level: str) -> dict[str, object]:
    """Translate the abstract level into the model's own parameter fields.

    An unknown level falls back to `off` without raising; a model with no
    card entry (or an `always_on`/`unknown` mode) gets `{}` — no invented
    parameters, whatever the level.
    """
    if level not in _LEVELS:
        level = _OFF
    mode = info.reasoning_mode
    if mode == "togglable":
        return _branch(info.reasoning_off if level == _OFF else info.reasoning_on)
    if mode == "togglable_default_off":
        # `low` counts as off: only medium and high turn the switch on.
        on = level in ("medium", "high")
        return _branch(info.reasoning_on if on else info.reasoning_off)
    if mode == "effort":
        return _effort(level)
    # always_on and unknown: never merge anything.
    return {}


def _branch(branch: Mapping[str, object]) -> dict[str, object]:
    """A fresh deep copy of an off/on branch, safe for the caller to mutate."""
    return {key: _copy(value) for key, value in branch.items()}


def _copy(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy(item) for key, item in value.items()}
    return value


def _effort(level: str) -> dict[str, object]:
    """The effort parameter: `reasoning_effort: <level>` for on-levels.

    `off` sends nothing. The card carries no parameter name for effort
    models, so the default `reasoning_effort` with the level as the value
    applies (deepseek-v4-flash, deepseek-v4-pro).
    """
    if level == _OFF:
        return {}
    return {"reasoning_effort": level}
