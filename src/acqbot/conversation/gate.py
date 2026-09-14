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
