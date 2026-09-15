"""Validation gate — stage 6 of the per-message loop (Section 7).

Every outbound message passes through here, scripted or generated. The rules are code, not
prompt instructions: a figure is either one of the four ladder values or it does not go out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from acqbot.models import LeadState

MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{3,7})")
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
KM_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d{4,7})\s?(?:km|kms|kilometres|kilometers)\b", re.I)
# Makes the gate recognises when checking that a generated message names only the seller's car.
KNOWN_MAKES = (
    "Toyota",
    "Mazda",
    "Hyundai",
    "Kia",
    "Ford",
    "Volkswagen",
    "Subaru",
    "Mitsubishi",
    "Nissan",
    "Honda",
    "Holden",
    "Isuzu",
    "Suzuki",
    "Jeep",
    "Tesla",
    "Lexus",
    "Audi",
    "BMW",
    "Mercedes",
    "Skoda",
    "Peugeot",
    "Renault",
    "Volvo",
    "Porsche",
    "Land Rover",
    "MG",
    "LDV",
    "GWM",
    "BYD",
)
MAKE_RE = re.compile(r"\b(" + "|".join(re.escape(m) for m in KNOWN_MAKES) + r")\b", re.I)
COMMITMENT = re.compile(
    r"\b(guarantee[ds]?|we will (definitely )?buy|we'?ll (definitely )?buy|committed to (buy|purchas)|purchase is confirmed|"
    r"deal is done|it'?s a done deal|cash (today|now|in hand)|unconditional)\b",
    re.I,
)
COMPETITION = re.compile(
    r"\b(other buyers?|another buyer|competing offers?|other offers?|other dealers?|another dealer|someone else is interested|"
    r"other (people|parties) (are )?interested|multiple offers|highest bidder|beat (any|their) offer|market is (flooded|hot))\b",
    re.I,
)
PRESSURE = re.compile(
    r"\b(act now|last chance|today only|don'?t miss (out|this)|hurry|limited time|right now or|final warning|now or never)\b",
    re.I,
)
ALLOWED_CAPS = {
    "LMCT",
    "PPSR",
    "VIN",
    "RWC",
    "ABN",
    "ACN",
    "SMS",
    "GPS",
    "ABS",
    "SRS",
    "ID",
    "OK",
    "AWD",
    "4WD",
    "AM",
    "PM",
    "EV",
    "SUV",
    "UTE",
    "GX",
    "GXL",
    "SR",
    "SR5",
    "XLT",
    "XL",
    "XLS",
    "GT",
    "ST",
    "SX",
    "ZR",
    "VX",
    "TSI",
    "RWD",
    "AUD",
}


@dataclass
class GateContext:
    stage: LeadState
    ladder: dict[str, float] | None = None  # present only at PRICED and beyond
    current_offer: float | None = None
    vehicle_year: int | None = None
    allowed_years: set[int] = field(default_factory=set)  # e.g. a contradicted claimed year being queried
    max_length: int = 2000
    today: date = field(default_factory=date.today)
    # Fact-sheet consistency (7: "no vehicle fact absent from the fact store") — used for generated text.
    vehicle_make: str | None = None
    vehicle_odometer_km: int | None = None
    allowed_odometers: set[int] = field(default_factory=set)


@dataclass
class GateResult:
    ok: bool
    violations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "violations": self.violations}


PRICE_STATES = {
    LeadState.PRICED,
    LeadState.OFFER_MADE,
    LeadState.NEGOTIATING,
    LeadState.ACCEPTED,
    LeadState.HANDOFF,
}


def validate(body: str, ctx: GateContext) -> GateResult:
    v: list[str] = []
    figures = [int(m.group(1).replace(",", "")) for m in MONEY_RE.finditer(body)]

    if ctx.stage not in PRICE_STATES and figures:
        v.append(f"figure before PRICED: {figures}")
    if ctx.stage in PRICE_STATES and figures:
        allowed = set()
        if ctx.ladder:
            allowed |= {int(round(x)) for x in ctx.ladder.values()}
        if ctx.current_offer is not None:
            allowed.add(int(round(ctx.current_offer)))
        bad = [f for f in figures if f not in allowed]
        if bad:
            v.append(f"figure not on the authorised ladder: {bad}")

    if COMMITMENT.search(body):
        v.append("binding commitment language")
    if COMPETITION.search(body):
        v.append("reference to other buyers or competing offers")
    if PRESSURE.search(body):
        v.append("pressure language")

    years = {int(y) for y in YEAR_RE.findall(body)}
    ok_years = {ctx.today.year, ctx.today.year + 1}
    if ctx.vehicle_year:
        ok_years.add(ctx.vehicle_year)
    ok_years |= set(ctx.allowed_years)
    stray = sorted(years - ok_years)
    if stray:
        v.append(f"year not in the fact sheet: {stray}")

    kms = {int(m.group(1).replace(",", "")) for m in KM_RE.finditer(body)}
    ok_kms = set(ctx.allowed_odometers)
    if ctx.vehicle_odometer_km is not None:
        ok_kms.add(int(ctx.vehicle_odometer_km))
    stray_kms = sorted(kms - ok_kms)
    if stray_kms:
        v.append(f"odometer figure not in the fact sheet: {stray_kms}")

    if ctx.vehicle_make:
        makes = {m.group(1).lower() for m in MAKE_RE.finditer(body)}
        other = sorted(makes - {ctx.vehicle_make.lower()})
        if other:
            v.append(f"vehicle make not in the fact sheet: {other}")

    if len(body) > ctx.max_length:
        v.append(f"length {len(body)} exceeds channel maximum {ctx.max_length}")
    if "!!" in body:
        v.append("tone: repeated exclamation")
    caps = [w for w in re.findall(r"\b[A-Z]{3,}\b", body) if w not in ALLOWED_CAPS]
    if caps:
        v.append(f"tone: shouting {caps[:3]}")
    if not body.strip():
        v.append("empty message")

    return GateResult(ok=not v, violations=v)
