"""Provider interfaces for pre-contact enrichment (Section 3.3).

Every provider sits behind one of these Protocols. The pipeline never imports a concrete provider;
`providers.py` builds the set from configuration. Real providers (Redbook, PPSR B2G, a NEVDIS
broker, Manheim/Pickles feeds) implement the same signatures and drop in with no pipeline changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol


class ProviderError(Exception):
    """A provider call failed. `retryable` tells the pipeline whether to back off and try again."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class VehicleHint:
    """What the seller claimed — real decoders ignore it; stubs use it to produce coherent data."""

    make: str
    model: str
    variant: str | None
    year: int
    odometer_km: int


@dataclass
class VinDecodeResult:
    vin: str
    make: str
    model: str
    variant: str | None
    year: int
    build_month: str | None  # YYYY-MM
    body: str | None
    factory_options: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PpsrResult:
    vin: str
    checked_at: datetime
    encumbered: bool
    encumbrance_amount_aud: float | None
    secured_parties: list[str]
    written_off: bool
    written_off_type: str | None  # repairable | statutory
    stolen: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegoResult:
    rego: str
    state: str
    status: str  # current | expired | unregistered | unknown
    expiry: date | None
    vin: str | None
    make: str | None = None
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuideResult:
    provider: str
    trade_low: float
    trade_high: float
    retail_low: float
    retail_high: float
    km_band: str
    as_of: date
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Comp:
    sold_at: date
    price_aud: float
    odometer_km: int
    variant: str | None
    match_quality: float  # 0..1 — how close a match to the subject vehicle
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompsResult:
    provider: str
    comps: list[Comp]
    as_of: date


class VinDecoder(Protocol):
    name: str

    def decode(self, vin: str, *, hint: VehicleHint | None = None) -> VinDecodeResult: ...


class PpsrChecker(Protocol):
    name: str

    def check(self, vin: str) -> PpsrResult: ...


class RegoLookup(Protocol):
    name: str

    def lookup(self, rego: str, state: str, *, hint: VehicleHint | None = None) -> RegoResult: ...


class GuideValuation(Protocol):
    name: str

    def lookup(
        self, make: str, model: str, variant: str | None, year: int, odometer_km: int
    ) -> GuideResult: ...


class AuctionComps(Protocol):
    name: str

    def search(
        self, make: str, model: str, variant: str | None, year: int, odometer_km: int, *, months: int = 6
    ) -> CompsResult: ...


@dataclass
class Providers:
    vin: VinDecoder
    ppsr: PpsrChecker
    rego: RegoLookup
    guide: GuideValuation
    comps: AuctionComps
