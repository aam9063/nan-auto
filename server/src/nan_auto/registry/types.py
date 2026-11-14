"""Model registry types: catalog entries, merged model info and the local card.

The shapes here are the registry's vocabulary, not the wire format: the catalog
client parses `/v1/models` into `CatalogEntry` and the merge produces
`ModelInfo`, so a change in the API's JSON stays inside one module. All value
types are frozen, following the `clef_client.types` house style.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CatalogEntry:
    """One entry of the `/v1/models` list, as the API reports it."""

    id: str
    mode: str
    object: str = "model"
    owned_by: str = ""
    created: int = 0
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ModelInfo:
    """One merged model: API limits plus the capability card from the YAML.

    Everything the API does not report (input modalities, tools, reasoning,
    premium, tier) comes from `config/models.yaml`; an unknown id keeps the
    generalist defaults below and never receives a tier.
    """

    id: str
    mode: str
    owned_by: str = ""
    created: int = 0
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    context: str = ""
    input: tuple[str, ...] = ("text",)
    tools: bool | str = True
    reasoning_mode: str = "unknown"
    reasoning_off: Mapping[str, object] = field(default_factory=dict)
    reasoning_on: Mapping[str, object] = field(default_factory=dict)
    premium: bool = False
    # The card can keep a model in the inventory while forbidding the policy
    # from choosing it (minimax-h3 had no measured data at design time).
    excluded: bool = False
    tier: str = ""


@dataclass(frozen=True)
class ModelCard:
    """The parsed `config/models.yaml`: capabilities, non-chat ids and tiers."""

    models: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    non_chat: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    tiers: Mapping[str, Sequence[str]] = field(default_factory=dict)


DEFAULT_CARD_PATH = Path(__file__).resolve().parents[4] / "config" / "models.yaml"
