"""Build the provider set from configuration. Real providers register here as they arrive."""

from __future__ import annotations

from functools import lru_cache

from acqbot.config import get_settings
from acqbot.enrichment.protocols import Providers
from acqbot.enrichment.stubs import (
    StubAuctionComps,
    StubGuideValuation,
    StubPpsrChecker,
    StubRegoLookup,
    StubVinDecoder,
)

_VIN = {"stub": StubVinDecoder}
_PPSR = {"stub": StubPpsrChecker}
_REGO = {"stub": StubRegoLookup}
_GUIDE = {"stub": StubGuideValuation}
_COMPS = {"stub": StubAuctionComps}


def _pick(registry: dict, name: str, kind: str):
    try:
        return registry[name]()
    except KeyError as exc:
        raise ValueError(f"unknown {kind} provider {name!r}; known: {sorted(registry)}") from exc


@lru_cache
def get_providers() -> Providers:
    s = get_settings()
    return Providers(
        vin=_pick(_VIN, s.vin_provider, "vin"),
        ppsr=_pick(_PPSR, s.ppsr_provider, "ppsr"),
        rego=_pick(_REGO, s.rego_provider, "rego"),
        guide=_pick(_GUIDE, s.guide_provider, "guide"),
        comps=_pick(_COMPS, s.comps_provider, "comps"),
    )


def reset_providers_cache() -> None:
    get_providers.cache_clear()
