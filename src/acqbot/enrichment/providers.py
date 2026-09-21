"""Build the provider set from configuration. Real providers register here as they arrive.

Every provider setting takes one of three kinds of value:

    stub      made-up but plausible data, for development, tests and demos. REFUSED in production:
              a stub PPSR passes every car, and a stub price guide invents a number a seller
              would be offered.
    manual    a person does this step in the console — runs the PPSR search, reads the rego,
              sets the price. The provider is None and the pipeline skips it. This is how the
              system runs before the data contracts exist: "humans occupying the not-yet-built
              stages" [12].
    <name>    a real provider, registered below as each contract lands.
"""

from __future__ import annotations

from functools import lru_cache

from acqbot.config import Settings, get_settings
from acqbot.enrichment.protocols import Providers
from acqbot.enrichment.stubs import (
    StubAuctionComps,
    StubGuideValuation,
    StubPpsrChecker,
    StubRegoLookup,
    StubVinDecoder,
)

STUB = "stub"
MANUAL = "manual"

_VIN = {STUB: StubVinDecoder}
_PPSR = {STUB: StubPpsrChecker}
_REGO = {STUB: StubRegoLookup}
_GUIDE = {STUB: StubGuideValuation}
_COMPS = {STUB: StubAuctionComps}

# Provider kind → the setting that chooses it, in the order the pipeline runs them.
KINDS: dict[str, str] = {
    "rego": "rego_provider",
    "vin": "vin_provider",
    "ppsr": "ppsr_provider",
    "guide": "guide_provider",
    "comps": "comps_provider",
}
# What each kind means to someone reading an error or the console.
KIND_LABEL = {
    "rego": "rego check",
    "vin": "VIN decode",
    "ppsr": "PPSR check",
    "guide": "price guide",
    "comps": "auction results",
}


class UnsafeConfiguration(RuntimeError):
    """The configuration would put made-up data in front of a real seller."""


def provider_names(settings: Settings) -> dict[str, str]:
    return {kind: getattr(settings, attr).strip().lower() for kind, attr in KINDS.items()}


def on_test_data(settings: Settings) -> list[str]:
    """Kinds running on stub data. Fine on a laptop; never in front of a seller."""
    return [kind for kind, name in provider_names(settings).items() if name == STUB]


def done_by_a_person(settings: Settings) -> list[str]:
    return [kind for kind, name in provider_names(settings).items() if name == MANUAL]


def pricing_is_manual(settings: Settings) -> bool:
    """No price data at all: a person sets every figure and the engine computes nothing.

    Both sources have to be manual. With either one connected the engine can price (it blends
    what it has), and the automation's ladder stays available."""
    names = provider_names(settings)
    return names["guide"] == MANUAL and names["comps"] == MANUAL


def assert_no_test_data(settings: Settings | None = None) -> None:
    """Refuse to run in production on stub data. Called when the API and the worker start, and
    again whenever providers are built, so there is no path to a seller that skips it."""
    cfg = settings or get_settings()
    stubs = on_test_data(cfg)
    if cfg.is_production and stubs:
        named = ", ".join(f"{KIND_LABEL[k]} (ACQBOT_{KINDS[k].upper()})" for k in stubs)
        raise UnsafeConfiguration(
            f"ACQBOT_ENVIRONMENT is production but these run on made-up test data: {named}. "
            f"Set each to '{MANUAL}' (a person does it in the console) or to a real provider."
        )


def _pick(registry: dict, name: str, kind: str):
    if name == MANUAL:
        return None
    try:
        return registry[name]()
    except KeyError as exc:
        known = sorted([*registry, MANUAL])
        raise ValueError(f"unknown {kind} provider {name!r}; known: {known}") from exc


@lru_cache
def get_providers() -> Providers:
    s = get_settings()
    assert_no_test_data(s)
    names = provider_names(s)
    return Providers(
        vin=_pick(_VIN, names["vin"], "vin"),
        ppsr=_pick(_PPSR, names["ppsr"], "ppsr"),
        rego=_pick(_REGO, names["rego"], "rego"),
        guide=_pick(_GUIDE, names["guide"], "guide"),
        comps=_pick(_COMPS, names["comps"], "comps"),
    )


def reset_providers_cache() -> None:
    get_providers.cache_clear()
