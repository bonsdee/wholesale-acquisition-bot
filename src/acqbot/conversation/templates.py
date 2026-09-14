"""Scripted messages — Phase 3 (no language model). Tone per Section 7.2: direct, unhurried, no
pressure language. Every template is versioned so `messages.model_version` records exactly which
wording produced a given outbound.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from acqbot.facts.fields import FieldSpec, spec_for

TEMPLATE_VERSION = "template:v1"


@dataclass
class Identity:
    dealership: str
    lmct: str
    agent: str


def _vehicle(sheet_get) -> str:
    year, make, model, variant = (
        sheet_get("year"),
        sheet_get("make"),
        sheet_get("model"),
        sheet_get("variant"),
    )
    bits = [str(year) if year else "", make or "", model or "", variant or ""]
    return " ".join(b for b in bits if b).strip()


def fmt_money(amount: float | int) -> str:
    return f"${int(round(float(amount))):,}"


def fmt_expiry(expires_at: datetime, tz: str) -> str:
    """e.g. '3:30pm on Wednesday 16 September' — built by hand so it works on Windows too."""
    local = expires_at.astimezone(ZoneInfo(tz))
    hour12 = local.hour % 12 or 12
    ampm = "am" if local.hour < 12 else "pm"
    return f"{hour12}:{local.minute:02d}{ampm} on {local.strftime('%A')} {local.day} {local.strftime('%B')}"


def disclosure(identity: Identity) -> str:
    return (
        f"Quick note before we start: this is an automated assistant from {identity.dealership} "
        f"(LMCT {identity.lmct}). A person from our team is available any time — just ask."
    )


def opening(
    identity: Identity, seller_name: str, sheet_get, first_ask: str, variants: tuple[str, ...] = ()
) -> str:
    first = (seller_name or "").split(" ")[0] or "there"
    vehicle = _vehicle(sheet_get)
    if not sheet_get("variant") and len(variants) >= 2:
        first_ask = f"Is it the {variants[0]} or the {variants[1]}?"
    return f"Hi {first}, {identity.agent} here from {identity.dealership}. {disclosure(identity)}\n\nWe're interested in your {vehicle}. {first_ask}"


def ask_field(spec: FieldSpec, *, preface: str = "") -> str:
    return f"{preface}{spec.ask}".strip()


def clarify(spec: FieldSpec) -> str:
    if spec.choices:
        return f"Sorry, I didn't catch that. {spec.label} — {' / '.join(spec.choices)}?"
    if spec.key == "odometer_km":
        return "Sorry, I didn't catch that — what's the odometer reading, in km? (e.g. 84,500)"
    if spec.key == "keys_count":
        return "Sorry — how many keys, as a number?"
    if spec.key == "rego":
        return "Sorry, I didn't catch that — what's the rego plate (e.g. 1AB2CD), or the 17-character VIN?"
    return f"Sorry, I didn't catch that. {spec.ask}"


def contradiction(field_key: str, claimed: Any, actual: Any, source: str) -> str:
    label = (spec_for(field_key).label if spec_for(field_key) else field_key).lower()
    src = {
        "ppsr": "the PPSR check",
        "vin": "the VIN decode",
        "rego": "the rego check",
        "photo": "the photos",
        "inspection": "inspection",
    }.get(source, source)
    return (
        f"One thing to check: the listing said {label} {claimed}, but {src} shows {actual}. Which is right?"
    )


def photos_partial(have: int, need: int) -> str:
    return f"Got {have} — could you send the rest? I need {need} in total: the six exterior angles, the interior, and the dash with the ignition on."


def verification_wait(identity: Identity) -> str:
    return "Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly."


def priced_pending_human(identity: Identity) -> str:
    return f"Thanks — I've got everything. {identity.agent} is working out a firm number and will send it through shortly."


def offer(amount: float, expires_at: datetime, sheet_get, identity: Identity, tz: str) -> str:
    return (
        f"Here's where we've landed: {fmt_money(amount)} for the {_vehicle(sheet_get)}, subject to inspection. "
        f"That's a firm offer and it's open until {fmt_expiry(expires_at, tz)} — after that the valuation inputs move and it lapses.\n\n"
        f"If it works, reply yes and {identity.agent} will book the inspection and sort payment. If not, no pressure — just let me know."
    )


def concession(amount: float, expires_at: datetime, tz: str) -> str:
    return (
        f"I can go to {fmt_money(amount)}. That's the most I've got room for on this one — same terms, "
        f"subject to inspection, open until {fmt_expiry(expires_at, tz)}."
    )


def at_ceiling(amount: float, identity: Identity) -> str:
    return (
        f"{fmt_money(amount)} is the ceiling for me — I can't go past it. If you'd like, {identity.agent} can take a "
        f"look personally and come back to you."
    )


def accepted(identity: Identity, seller_name: str) -> str:
    first = (seller_name or "").split(" ")[0] or "there"
    return f"Great — {identity.agent} will be in touch to book the inspection and confirm the details. Thanks {first}."


def rejected_close(expires_at: datetime, tz: str) -> str:
    return f"No problem — thanks for your time. If anything changes before {fmt_expiry(expires_at, tz)}, the offer stands until then."


def human_answer(identity: Identity) -> str:
    return (
        f"Straight answer: you're talking to an automated assistant run by {identity.dealership}. "
        f"I'll hand you over — a person from our team will pick this up from here."
    )


def stop_ack() -> str:
    return "Understood — I won't message again."


def template_hash(template_id: str, **vars: Any) -> str:
    payload = template_id + "|" + "|".join(f"{k}={vars[k]}" for k in sorted(vars))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]
