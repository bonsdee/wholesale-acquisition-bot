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
    # "hurry up" is pressure; "no hurry" and "no rush" are the opposite and must not be rejected.
    r"\b(act now|last chance|today only|don'?t miss (out|this)|hurry up|in a hurry|limited time|"
    r"right now or|final warning|now or never|before it'?s too late)\b",
    re.I,
)
# A claim that this is the most on offer. The automation NEVER knows that: above its last authorised
# concession sits a ceiling only a person can approve, so "the best I can do" is untrue every time
# the model says it — and a seller told it, then offered more, has been misled (Section 1, ACL s18).
# Phase 5 took exactly this out of the scripted concession; on the first full live run (21 Sep) the
# model put it back — "that's the best i can put forward" at step_2 — and nothing stopped it.
#
# Deliberately narrow. "is as far as I can take it ON MY OWN" (the scripted ceiling message) and
# "going higher isn't something I can decide" are true and must pass; only claims of a limit do not.
FINALITY = re.compile(
    r"\b(best (i|we) can (do|offer|manage|go|put forward|give)|"
    r"(my|our|the) (best|final|top|highest) (offer|price|number|figure)|"
    r"final (offer|number|price|figure)|"
    r"(can'?t|cannot|can not) go (any )?(higher|further)|"
    r"as good as it gets|take it or leave it|"
    r"most (i|we)('ve| have)? got room for|(that'?s|this is) (my|our|the) limit|"
    r"as high as (i|we) can go)\b",
    re.I,
)
# A claim that there is no room to move — true at the automation's last step, false before it.
# Found by reading the second live run (21 Sep): at the FIRST concession the model told two sellers
# "it's not something I can move beyond that" and "that's not something I can move on" — and then
# offered more one message later. The model cannot tell step_1 from step_2; this gate can, because
# it holds the ladder and the figure being presented. Only enforced while more room exists.
NO_ROOM = re.compile(
    r"\b((can'?t|cannot|can not|unable to|not able to) (move|go|budge|shift|stretch)"
    r"( it| the (price|number|figure|offer))?( any)? (further|higher|beyond|past|up)|"
    r"not something (i|we) can (move|budge|shift|stretch|go|decide)|"
    r"not (my|our) (call|decision)|"
    r"(isn'?t|is not) (up to|for) (me|us)|"
    r"no (more )?room (to move|left)|"
    r"as (far|high) as (i|we) can (go|take it)|"
    r"where (i|we) (have to |must )?stop)",
    re.I,
)


def _more_room(ctx: GateContext) -> bool:
    """Whether the automation itself still has a higher authorised step than the figure in play.

    While it does, "I can't move beyond that" is false. At the last automated step (step_2) the
    honest "going further isn't my decision" becomes true, and must be allowed through."""
    if ctx.sole_figure is None or not ctx.ladder or "step_2" not in ctx.ladder:
        return False
    return int(round(ctx.sole_figure)) < int(round(ctx.ladder["step_2"]))
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
    # Australian state and territory codes — "registered in VIC" is not shouting.
    "NSW",
    "VIC",
    "QLD",
    "TAS",
    "ACT",
    "NT",
    "REGO",
    "STOP",  # the Spam Act opt-out keyword; shouting it is the convention, not a tone problem
    "VICROADS",
    "EOFY",
    "LPG",
    "DPF",
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
    # Capitals that are part of this vehicle's own name — trim levels are open-ended (GLS, GXL,
    # SR5, XLT, VTi...) and a fixed list will always be missing one. Naming the car is not shouting.
    allowed_caps: set[str] = field(default_factory=set)
    # Phase 5. Once the model words offers, "on the ladder" is not a tight enough rule: told to
    # present the opening, a model that writes the ceiling instead has written a figure the gate
    # would wave through. When code is presenting a specific amount it names that amount here and
    # nothing else passes — not even another authorised step.
    sole_figure: float | None = None
    # Exact strings the message must contain, supplied by code: the amount as we write it and the
    # expiry as we computed it. The model may word everything around them and nothing inside them.
    must_include: list[str] = field(default_factory=list)


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
        if ctx.sole_figure is not None:
            allowed = {int(round(ctx.sole_figure))}
            bad = [f for f in figures if f not in allowed]
            if bad:
                v.append(f"figure other than the one being presented ({allowed.pop()}): {bad}")
        else:
            allowed = set()
            if ctx.ladder:
                allowed |= {int(round(x)) for x in ctx.ladder.values()}
            if ctx.current_offer is not None:
                allowed.add(int(round(ctx.current_offer)))
            bad = [f for f in figures if f not in allowed]
            if bad:
                v.append(f"figure not on the authorised ladder: {bad}")

    for required in ctx.must_include:
        if required and required not in body:
            v.append(f"must state exactly: {required!r}")

    if COMMITMENT.search(body):
        v.append("binding commitment language")
    if COMPETITION.search(body):
        v.append("reference to other buyers or competing offers")
    if PRESSURE.search(body):
        v.append("pressure language")
    if FINALITY.search(body):
        v.append(
            "claims this is the best, final or highest figure — it is not known to be: a person may "
            "still approve more, so say only that going further is not your decision"
        )
    # Neutral feedback on purpose: telling the model "there IS more room" would invite it to hint
    # at it, which is misleading in the other direction.
    elif _more_room(ctx) and NO_ROOM.search(body):
        v.append(
            "states a limit on the figure — make no statement about whether it can or cannot go "
            "higher; just give the figure"
        )

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
    permitted = ALLOWED_CAPS | {c.upper() for c in ctx.allowed_caps}
    caps = [w for w in re.findall(r"\b[A-Z]{3,}\b", body) if w not in permitted]
    if caps:
        v.append(f"tone: shouting {caps[:3]}")
    if not body.strip():
        v.append("empty message")

    return GateResult(ok=not v, violations=v)
