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
    # This is said at step_1 as well as step_2, so it must not claim to be the last word: there are
    # two authorised concessions and a ceiling above them, and a seller who is told "that's the most
    # I've got room for" and then offered more has been misled — which is the conduct Section 1 drops
    # the fake-competition mechanism to avoid.
    return (
        f"I can go to {fmt_money(amount)} — same terms, subject to inspection, open until "
        f"{fmt_expiry(expires_at, tz)}."
    )


def at_ceiling(amount: float, identity: Identity) -> str:
    # Nor is THIS the ceiling: it is as far as the automation goes, and a person may yet approve the
    # real one. So it says what is true — this is not the bot's call — and promises nothing.
    return (
        f"{fmt_money(amount)} is as far as I can take it on my own. Going further isn't my call, so "
        f"{identity.agent} will have a look and come back to you."
    )


def accepted(identity: Identity, seller_name: str) -> str:
    first = (seller_name or "").split(" ")[0] or "there"
    return f"Great — {identity.agent} will be in touch to book the inspection and confirm the details. Thanks {first}."


def nudge_discovery(identity: Identity, asked: str | None) -> str:
    """A seller has gone quiet mid-discovery. One short line, no guilt, an easy way out.

    Deliberately not a question they have already been asked twice — the gate would pass a nagging
    message, so the restraint has to live in the wording."""
    if asked:
        return (
            f"No rush at all — just checking you saw the question about {asked}. "
            "If you'd rather leave it here, that's completely fine, just say so."
        )
    return (
        "No rush — just checking in on the car. If you'd rather leave it here, that's completely "
        "fine, just say so."
    )


def nudge_offer(identity: Identity) -> str:
    """The offer is live and they have not replied. States nothing new and adds no deadline: the
    real one was given with the offer and repeating it starts to read as pressure."""
    return (
        f"Just checking in — that offer still stands. If you'd like to go ahead, say the word and "
        f"{identity.agent} will sort the inspection. If not, no hard feelings."
    )


def stalled_close(identity: Identity) -> str:
    return (
        "I'll leave it there so I'm not clogging up your messages. If you change your mind about "
        "selling, reply any time and we'll pick it back up."
    )


def offer_lapsed(identity: Identity) -> str:
    """Said when the 48 hours run out. Scripted, and carries no figure: the number is gone, which is
    the whole point of a real expiry, and a message that restates it is a message that softens it."""
    return (
        "The 48 hours on that offer are up, so it's lapsed. "
        f"If the car's still available and you'd like another look at it, say the word and {identity.agent} "
        "will re-run the numbers."
    )


def rejected_close(expires_at: datetime, tz: str) -> str:
    return f"No problem — thanks for your time. If anything changes before {fmt_expiry(expires_at, tz)}, the offer stands until then."


def human_answer(identity: Identity) -> str:
    return (
        f"Straight answer: you're talking to an automated assistant run by {identity.dealership}. "
        f"I'll hand you over — a person from our team will pick this up from here."
    )


def sms_first_contact(identity: Identity) -> str:
    """Prefixed to the first SMS on a migrated thread.

    Under the Spam Act 2003 a commercial electronic message must identify the sender and carry a
    low-cost way to stop it. The Section 8 disclosure covers the FIRST Messenger message — but a
    seller whose conversation moves to SMS otherwise gets a text from an unknown number with
    neither. The identification and the opt-out have to travel with the channel change."""
    return (
        f"{identity.dealership} (LMCT {identity.lmct}) here, carrying on our chat from Marketplace. "
        f"Reply STOP any time and I'll leave you alone.\n\n"
    )


def stop_ack() -> str:
    return "Understood — I won't message again."


def template_hash(template_id: str, **vars: Any) -> str:
    payload = template_id + "|" + "|".join(f"{k}={vars[k]}" for k in sorted(vars))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]
