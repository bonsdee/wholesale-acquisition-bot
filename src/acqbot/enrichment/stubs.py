"""Deterministic stub providers.

Every stub derives its output from a hash of its inputs, so the same lead always enriches the same
way and tests are reproducible. A handful of "magic" suffixes let the simulator and tests steer
outcomes without special-casing the pipeline:

    VIN ends with  W   → PPSR: written off (repairable)
    VIN ends with  S   → PPSR: stolen
    VIN ends with  E   → PPSR: encumbered (finance owing)
    VIN ends with  C   → VIN decode returns year - 1 (contradicts the seller's claimed year)
    rego contains  99  → registration expired
    rego contains  00  → unregistered

Anything else decodes as clean. None of this is meaningful outside development.
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, date, datetime, timedelta

from acqbot.enrichment import catalog
from acqbot.enrichment.protocols import (
    Comp,
    CompsResult,
    GuideResult,
    PpsrResult,
    ProviderError,
    RegoResult,
    VehicleHint,
    VinDecodeResult,
)

_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


def _seed(*parts: object) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def _rng(*parts: object) -> random.Random:
    return random.Random(_seed(*parts))


def synthetic_vin(*parts: object) -> str:
    rng = _rng("vin", *parts)
    return "".join(rng.choice(_VIN_CHARS) for _ in range(17))


# --- guide pricing model shared by the guide stub and the simulator's asking prices ------------


def stub_trade_mid(make: str, model: str, year: int, odometer_km: int, *, today: date | None = None) -> float:
    """Crude depreciation curve: -18% year one, -11% each year after, floored at 8% of new."""
    today = today or date.today()
    entry = catalog.find(make, model)
    new_price = entry.new_price_aud if entry else catalog.DEFAULT_NEW_PRICE
    age = max(0, today.year - year)
    value = new_price * (0.82 if age >= 1 else 0.95)
    for _ in range(max(0, age - 1)):
        value *= 0.89
    value = max(value, new_price * 0.08)
    expected_km = max(1, age) * 15_000
    km_delta = odometer_km - expected_km
    if km_delta > 0:
        value *= max(0.55, 1 - 0.015 * (km_delta / 10_000))
    else:
        value *= min(1.12, 1 + 0.01 * (-km_delta / 10_000))
    # Trade sits below retail by roughly 15%.
    return round(value * 0.85, -1)


class StubVinDecoder:
    name = "stub-vin"

    def decode(self, vin: str, *, hint: VehicleHint | None = None) -> VinDecodeResult:
        if hint is None:
            raise ProviderError("stub VIN decoder needs a hint (real decoders do not)", retryable=False)
        rng = _rng("decode", vin)
        entry = catalog.find(hint.make, hint.model)
        variants = entry.variants if entry else ("Base", "Mid", "Top")
        variant = hint.variant if hint.variant in variants else rng.choice(variants)
        year = hint.year - 1 if vin.endswith("C") else hint.year
        options_pool = ["tow bar", "roof racks", "tinted windows", "premium audio", "sunroof", "leather trim"]
        options = rng.sample(options_pool, k=rng.randint(0, 3))
        return VinDecodeResult(
            vin=vin,
            make=entry.make if entry else hint.make,
            model=entry.model if entry else hint.model,
            variant=variant,
            year=year,
            build_month=f"{year}-{rng.randint(1, 12):02d}",
            body=entry.body if entry else None,
            factory_options=options,
            raw={"stub": True},
        )


class StubPpsrChecker:
    name = "stub-ppsr"

    def check(self, vin: str) -> PpsrResult:
        rng = _rng("ppsr", vin)
        last = vin[-1]
        encumbered = last == "E"
        written_off = last == "W"
        stolen = last == "S"
        return PpsrResult(
            vin=vin,
            checked_at=datetime.now(UTC),
            encumbered=encumbered,
            encumbrance_amount_aud=float(rng.randint(50, 250) * 100) if encumbered else None,
            secured_parties=[rng.choice(["Toyota Finance", "Macquarie Leasing", "Latitude", "Pepper Money"])]
            if encumbered
            else [],
            written_off=written_off,
            written_off_type="repairable" if written_off else None,
            stolen=stolen,
            raw={"stub": True, "search_number": f"STUB-{_seed(vin) % 10_000_000:07d}"},
        )


class StubRegoLookup:
    name = "stub-rego"

    def lookup(self, rego: str, state: str, *, hint: VehicleHint | None = None) -> RegoResult:
        rng = _rng("rego", rego, state)
        if "00" in rego:
            status, expiry = "unregistered", None
        elif "99" in rego:
            status, expiry = "expired", date.today() - timedelta(days=rng.randint(10, 300))
        else:
            status, expiry = "current", date.today() + timedelta(days=rng.randint(15, 360))
        return RegoResult(
            rego=rego,
            state=state,
            status=status,
            expiry=expiry,
            vin=synthetic_vin(rego, state),
            make=hint.make if hint else None,
            model=hint.model if hint else None,
            raw={"stub": True},
        )


class StubGuideValuation:
    name = "stub-guide"

    def lookup(self, make: str, model: str, variant: str | None, year: int, odometer_km: int) -> GuideResult:
        mid = stub_trade_mid(make, model, year, odometer_km)
        band = 0.06
        return GuideResult(
            provider=self.name,
            trade_low=round(mid * (1 - band), -1),
            trade_high=round(mid * (1 + band), -1),
            retail_low=round(mid * 1.12, -1),
            retail_high=round(mid * 1.28, -1),
            km_band=f"{(odometer_km // 20_000) * 20}k-{(odometer_km // 20_000) * 20 + 20}k",
            as_of=date.today(),
            raw={"stub": True},
        )


class StubAuctionComps:
    name = "stub-comps"

    def search(
        self, make: str, model: str, variant: str | None, year: int, odometer_km: int, *, months: int = 6
    ) -> CompsResult:
        rng = _rng("comps", make, model, year, odometer_km // 5_000)
        mid = stub_trade_mid(make, model, year, odometer_km)
        entry = catalog.find(make, model)
        variants = entry.variants if entry else ("Base", "Mid", "Top")
        comps: list[Comp] = []
        for _ in range(rng.randint(4, 7)):
            v = rng.choice(variants)
            comps.append(
                Comp(
                    sold_at=date.today() - timedelta(days=rng.randint(3, months * 30)),
                    price_aud=round(mid * rng.uniform(0.90, 1.10), -1),
                    odometer_km=max(0, int(odometer_km * rng.uniform(0.75, 1.25))),
                    variant=v,
                    match_quality=round(
                        rng.uniform(0.85, 1.0) if v == variant else rng.uniform(0.55, 0.8), 2
                    ),
                    source=rng.choice(["stub-manheim", "stub-pickles"]),
                    raw={"stub": True},
                )
            )
        return CompsResult(provider=self.name, comps=comps, as_of=date.today())
