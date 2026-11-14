"""Policy: pick the model and the abstract reasoning level, and say why.

NAU-06, spec 7.2 (decision table), 7.3 (profiles), 6.4 (when in doubt, tier
up) and 5.3 (fail open): the policy is a pure function of the Clef decision,
the request features, the inventory and the profile — no network, no clock,
no module-level mutable state. It ranks the capability filter's candidates
with `score = fit*q - latency*l - quota_penalty` and returns the winner with
a short reason string built only from facts, in the shape spec 8 uses for
the `x-nan-auto-reason` header. The reasoning level stays abstract
(`off`, `low`, `medium`, `high`); mapping it to model parameters is NAU-09.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime

from nan_auto.clef_client.types import Decision
from nan_auto.registry.state import ModelHealth
from nan_auto.registry.types import ModelCard, ModelInfo
from nan_auto.router_core.capabilities import (
    BALANCED_PROFILE,
    Profile,
    filter_candidates,
    resolve_profile,
)
from nan_auto.router_core.features import RequestFeatures

# Initial tier ladder [HIPÓTESIS], from the card; "one tier up" follows it.
_TIER_ORDER = ("t1_fast", "t2_generalist", "t3_max")
# Tier → latency cost, unmeasured design-time priors [HIPÓTESIS]; the quality
# matrix (NAU-14) replaces them later.
_LATENCY_BY_TIER: Mapping[str, float] = {
    "t1_fast": 0.2,
    "t2_generalist": 0.4,
    "t3_max": 0.6,
}
_DEFAULT_LATENCY = 0.5
# Fit decays per preference slot so the table's order survives scoring: the
# first explicit id or the first preferred tier scores 1.0, later slots less.
_TIER_FIT = {1: 1.0, 2: 0.75, 3: 0.5}
_ID_FIT = {1: 1.0, 2: 0.85, 3: 0.7}
# Observed quota or failure pressure; the registry reports it via health.
_QUOTA_PENALTY = 0.25
# Beyond this the summarize row prefers the 1M-context models [HIPÓTESIS].
LONG_CONTEXT_TOKENS = 100_000

_ON = "on"
_OFF = "off"
_NEEDS = "needs"


@dataclass(frozen=True)
class _Row:
    """One decision-table line: preference plus the reasoning suggestion.

    `ids` are explicit model preferences (they win over tiers); `tiers` are
    preferred tier names in order; `reasoning` is `on`, `off` or `needs`
    (follow `needs_reasoning`).
    """

    ids: tuple[str, ...] = ()
    tiers: tuple[str, ...] = ()
    reasoning: str = _NEEDS


@dataclass(frozen=True)
class PolicyResult:
    """The election plus its facts: ranked candidates, rejections, fallback."""

    model: str | None
    reasoning: str
    reason: str
    candidates: tuple[str, ...]
    rejected: Mapping[str, str] = field(default_factory=dict)
    fallback: bool = False


def decide(
    decision: Decision,
    features: RequestFeatures,
    inventory: Mapping[str, ModelInfo],
    *,
    profile: str = BALANCED_PROFILE,
    default_model: str | None = None,
    health: Mapping[str, ModelHealth] | None = None,
    now: datetime | None = None,
    card: ModelCard | None = None,
) -> PolicyResult:
    """Choose model + reasoning level for one request, or fail open.

    No candidate left is never an error: with a profile default the election
    falls back to it (spec 5.3), and without one the result simply carries no
    model, so the caller can decide its own open behavior.
    """
    resolved = resolve_profile(profile)
    task = decision.task.label
    difficulty = decision.difficulty.level()
    row = _table_row(task, difficulty, features)
    # Spec 6.4: low task confidence or a split difficulty read has no second
    # query to fall back on — exactly one tier up, more capability, never less.
    if decision.task.confidence < 0.60 or decision.difficulty.is_split():
        row = _bump(row)
    reasoning_on = _reasoning_on(row, resolved, difficulty, decision)
    level = _level(reasoning_on, task, difficulty)
    reason = f"{task} d={difficulty} reasoning={_ON if reasoning_on else _OFF}"

    filtered = filter_candidates(
        features,
        inventory,
        profile=profile,
        difficulty=difficulty,
        health=health,
        now=now,
        card=card,
    )
    ranked = _rank(row, filtered.candidates, inventory, resolved, health)
    if ranked:
        return PolicyResult(
            model=ranked[0],
            reasoning=level,
            reason=reason,
            candidates=ranked,
            rejected=filtered.rejected,
        )
    if default_model is not None:
        return PolicyResult(
            model=default_model,
            reasoning=level,
            reason=f"{reason} fallback={default_model}",
            candidates=(),
            rejected=filtered.rejected,
            fallback=True,
        )
    return PolicyResult(
        model=None,
        reasoning=level,
        reason=f"{reason} no_candidate",
        candidates=(),
        rejected=filtered.rejected,
        fallback=True,
    )


def _table_row(task: str, difficulty: int, features: RequestFeatures) -> _Row:
    """The spec 7.2 row for this request; unlisted combinations get a default.

    The capability-hungry rows win first (hard coding, long-context
    summarize), then the tools row (a request carrying tools needs verified
    tool calling whatever its task), then the task rows.
    """
    if task == "coding_generate" and difficulty >= 3:
        return _Row(ids=("glm5.3", "deepseek-v4-flash", "qwen3.6"), reasoning=_ON)
    if task == "summarize_or_extract" and features.est_tokens >= LONG_CONTEXT_TOKENS:
        return _Row(ids=("deepseek-v4-flash", "glm5.3-flash"), reasoning=_OFF)
    if features.has_tools or task == "agentic_planning":
        return _Row(tiers=("t2_generalist", "t3_max"), reasoning=_NEEDS)
    if task in ("chat_casual", "factual_qa"):
        if difficulty <= 1:
            return _Row(tiers=("t1_fast",), reasoning=_OFF)
        if difficulty == 2:
            return _Row(tiers=("t2_generalist",), reasoning=_OFF)
        return _Row(tiers=("t2_generalist", "t3_max"), reasoning=_NEEDS)
    if task == "translate_or_rewrite" and difficulty <= 2:
        return _Row(tiers=("t1_fast", "t2_generalist"), reasoning=_OFF)
    if task == "creative_writing":
        return _Row(tiers=("t2_generalist",), reasoning=_OFF)
    if task == "coding_generate":
        return _Row(tiers=("t2_generalist",), reasoning=_OFF)
    if task in ("coding_debug_or_review", "math_or_logic") and difficulty >= 2:
        return _Row(tiers=("t2_generalist", "t3_max"), reasoning=_ON)
    if task == "summarize_or_extract":
        return _Row(tiers=("t1_fast", "t2_generalist"), reasoning=_OFF)
    return _Row(tiers=("t2_generalist", "t3_max"), reasoning=_NEEDS)


def _reasoning_on(row: _Row, profile: Profile, difficulty: int, decision: Decision) -> bool:
    """Table suggestion bent by the profile's reasoning rule (spec 7.3)."""
    if profile.reasoning_rule == "off_unless_hard":
        return difficulty >= 4
    if row.reasoning == _ON:
        base = True
    elif row.reasoning == _OFF:
        base = False
    else:
        base = decision.needs_reasoning.is_positive
    if profile.reasoning_rule == "on_when_hard":
        return base or difficulty >= 2
    return base


def _level(on: bool, task: str, difficulty: int) -> str:
    """Abstract reasoning level; only the coding row reaches for `low`."""
    if on:
        return "high"
    if task == "coding_generate" and difficulty == 2:
        return "low"
    return "off"


def _bump(row: _Row) -> _Row:
    """Spec 6.4: when in doubt, exactly one tier up — more capability, never less."""
    tiers = tuple(_tier_up(tier) for tier in row.tiers)
    if row.ids:
        tiers = row.tiers + (() if "t3_max" in row.tiers else ("t3_max",))
    return replace(row, tiers=tiers)


def _tier_up(tier: str) -> str:
    """The next tier in the ladder; the top tier stays where it is."""
    if tier in _TIER_ORDER:
        return _TIER_ORDER[min(_TIER_ORDER.index(tier) + 1, len(_TIER_ORDER) - 1)]
    return tier


def _rank(
    row: _Row,
    candidates: tuple[str, ...],
    inventory: Mapping[str, ModelInfo],
    profile: Profile,
    health: Mapping[str, ModelHealth] | None,
) -> tuple[str, ...]:
    """Score every candidate; ties resolve by inventory order, so the
    returned order is stable and reproducible across identical calls."""
    scores = [
        (index, model_id, _score(row, inventory[model_id], profile, health))
        for index, model_id in enumerate(candidates)
    ]
    scores.sort(key=lambda item: (-item[2], item[0]))
    return tuple(model_id for _, model_id, _ in scores)


def _score(
    row: _Row,
    info: ModelInfo,
    profile: Profile,
    health: Mapping[str, ModelHealth] | None,
) -> float:
    """`score = fit*q - latency*l - quota_penalty` (spec 7.3)."""
    fit = _fit(row, info)
    latency = _LATENCY_BY_TIER.get(info.tier, _DEFAULT_LATENCY)
    state = health.get(info.id) if health is not None else None
    quota = _QUOTA_PENALTY if state is not None and state.failures > 0 else 0.0
    return fit * profile.q_weight - latency * profile.l_weight - quota


def _fit(row: _Row, info: ModelInfo) -> float:
    """How well the model matches the row's preference slots."""
    if info.id in row.ids:
        return _ID_FIT[min(row.ids.index(info.id) + 1, max(_ID_FIT))]
    for position, tier in enumerate(row.tiers, start=1):
        if info.tier == tier:
            return _TIER_FIT.get(min(position, max(_TIER_FIT)), 0.0)
    return 0.0
