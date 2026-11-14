"""Capability filter: reduce the inventory to viable candidates by facts.

NAU-06, spec 7: the filter is a pure function of the request features, the
merged inventory, the profile and the difficulty — no network, no clock, no
module-level mutable state. Its output is facts only: the surviving candidate
ids in inventory order and a short machine-readable reason code for every
model it dropped. Rejection reasons never contain prompt text or user data.

The card (`config/models.yaml`) adds what the API does not report; the filter
reads it through the already-merged `ModelInfo` fields, except for the
json-schema note, which only exists as card text.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from nan_auto.registry.state import ModelHealth
from nan_auto.registry.types import ModelCard, ModelInfo
from nan_auto.router_core.features import RequestFeatures

# The card marks json-schema support with this phrase in `notes` (deepseek-v4
# flash at design time); the filter matches the phrase, not the model id.
_JSON_SCHEMA_UNSUPPORTED = "json_schema unsupported"

# Machine-readable rejection codes; each names one fact, in check order.
NO_IMAGE_SUPPORT = "no_image_support"
NO_AUDIO_SUPPORT = "no_audio_support"
CONTEXT_TOO_SMALL = "context_too_small"
NO_JSON_SCHEMA = "no_json_schema"
PREMIUM_NOT_ALLOWED = "premium_not_allowed"
EXCLUDED_FROM_POLICY = "excluded_from_policy"
UNAVAILABLE = "unavailable"
XML_TOOLS = "xml_tools"


@dataclass(frozen=True)
class Profile:
    """One policy profile (spec 7.3).

    `premium_min_difficulty` is the lowest difficulty that may spend premium
    quota; None means premium is never allowed. `reasoning_rule` names how the
    profile bends the decision table's reasoning suggestion (see policy).
    """

    q_weight: float
    l_weight: float
    premium_min_difficulty: int | None
    reasoning_rule: str


PROFILES: Mapping[str, Profile] = {
    "nan-auto": Profile(
        q_weight=1.0, l_weight=0.5, premium_min_difficulty=3, reasoning_rule="needs_reasoning"
    ),
    "nan-auto-fast": Profile(
        q_weight=0.6, l_weight=1.0, premium_min_difficulty=None, reasoning_rule="off_unless_hard"
    ),
    "nan-auto-quality": Profile(
        q_weight=1.0, l_weight=0.2, premium_min_difficulty=0, reasoning_rule="on_when_hard"
    ),
}
BALANCED_PROFILE = "nan-auto"


def resolve_profile(profile: str) -> Profile:
    """Return the named profile; an unknown id falls back to the balanced one."""
    return PROFILES.get(profile) or PROFILES[BALANCED_PROFILE]


@dataclass(frozen=True)
class FilterResult:
    """The survivors plus why everyone else was dropped.

    `candidates` keeps the inventory order, so downstream selection is stable.
    `penalized` lists models kept only as a last resort (XML tool calling when
    the request carries tools): they are candidates when nothing else survives
    and appear in `rejected` with `xml_tools` otherwise.
    """

    candidates: tuple[str, ...]
    rejected: Mapping[str, str]
    penalized: tuple[str, ...] = ()


def filter_candidates(
    features: RequestFeatures,
    inventory: Mapping[str, ModelInfo],
    *,
    profile: str = BALANCED_PROFILE,
    difficulty: int = 0,
    health: Mapping[str, ModelHealth] | None = None,
    now: datetime | None = None,
    card: ModelCard | None = None,
) -> FilterResult:
    """Reduce the inventory to the models that can serve this request.

    Hard rules, checked in order: input modality (image, audio), context
    size, output format, premium per profile, policy exclusion and health
    availability. A model failing any of them is out with one reason code.
    When the request carries tools, models whose card says `tools: xml` are
    penalized: they stay candidates only if nothing else survives.
    """
    rejected: dict[str, str] = {}
    survivors: list[str] = []
    penalized: list[str] = []
    for model_id, info in inventory.items():
        reason = _hard_rejection(features, info, profile, difficulty, health, now, card)
        if reason is not None:
            rejected[model_id] = reason
            continue
        if features.has_tools and info.tools == "xml":
            penalized.append(model_id)
            continue
        survivors.append(model_id)
    if survivors or not penalized:
        for model_id in penalized:
            rejected[model_id] = XML_TOOLS
        candidates = tuple(survivors)
    else:
        # Nothing verified survives: XML tool calling is penalized, not banned.
        candidates = tuple(penalized)
    return FilterResult(candidates=candidates, rejected=rejected, penalized=tuple(penalized))


def _hard_rejection(
    features: RequestFeatures,
    info: ModelInfo,
    profile: str,
    difficulty: int,
    health: Mapping[str, ModelHealth] | None,
    now: datetime | None,
    card: ModelCard | None,
) -> str | None:
    """The first fact that disqualifies the model, or None when it survives."""
    if features.has_image and "image" not in info.input:
        return NO_IMAGE_SUPPORT
    if features.has_audio and "audio" not in info.input:
        return NO_AUDIO_SUPPORT
    if info.max_input_tokens is not None and features.est_tokens > info.max_input_tokens:
        return CONTEXT_TOO_SMALL
    if features.response_format == "json_schema" and _lacks_json_schema(info.id, card):
        return NO_JSON_SCHEMA
    if info.premium and not _premium_allowed(profile, difficulty):
        return PREMIUM_NOT_ALLOWED
    if info.excluded:
        return EXCLUDED_FROM_POLICY
    if health is not None and not _is_available(health, info.id, now):
        return UNAVAILABLE
    return None


def _premium_allowed(profile: str, difficulty: int) -> bool:
    """Premium only inside the profile's difficulty window (spec 7.3)."""
    minimum = resolve_profile(profile).premium_min_difficulty
    return minimum is not None and difficulty >= minimum


def _is_available(health: Mapping[str, ModelHealth], model_id: str, now: datetime | None) -> bool:
    """Respect the registry's health state; an unknown model is available."""
    state = health.get(model_id)
    return state is None or state.is_available(now)


def _lacks_json_schema(model_id: str, card: ModelCard | None) -> bool:
    """True when the card's notes say the model cannot emit json_schema."""
    if card is None:
        return False
    notes = card.models.get(model_id, {}).get("notes", "")
    return isinstance(notes, str) and _JSON_SCHEMA_UNSUPPORTED in notes
