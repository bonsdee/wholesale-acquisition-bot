"""Output schemas for the two model calls, and the code that refuses to trust them blindly.

The extraction model returns every value as a short string in a fixed wire format; `coerce_fact`
turns each one into the fact-store shape the rest of the system already uses (the same shapes the
Phase 3 parsers produce), and drops anything it cannot validate. Nothing the model says reaches
the fact store without passing through here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from acqbot.conversation.extract import (
    PHONE,
    VIN_RE,
    parse_identifier,
    parse_odometer,
)
from acqbot.models import LeadState

# --- vocabulary --------------------------------------------------------------------------------

FIELD_WIRE: dict[str, str] = {
    "rego": "the plate exactly as written, letters and digits only (e.g. 1AB2CD, ABC123)",
    "vin": "the 17-character VIN, if given",
    "odometer_km": "integer kilometres, digits only (convert '85k' to 85000; '84,500' to 84500)",
    "service_history": "full | partial | none",
    "finance_owing": "none | owing | owing:<amount in dollars, digits only>",
    "write_off_status": "none | written_off | written_off:<type, e.g. hail, repairable, statutory>",
    "panel_paint_condition": (
        "excellent | good | fair | poor — a grade the seller states wins; otherwise infer only from clearly "
        "described damage (dents/scrapes → fair, rust/hail/respray needed → poor, minor marks → good)"
    ),
    "mechanical_faults": "none | <fault>; <fault>; ... (add 'warning light' as an item if any dash light is on)",
    "tyre_condition": "new | good | worn | replace",
    "keys_count": "integer",
    "rego_status": "current | current:<YYYY-MM when it expires> | expired | unregistered",
    "variant": "the trim or variant name as the seller wrote it (e.g. GXL, SR5, Ascent Sport)",
    "year": "four-digit model year, only if the seller states or corrects it",
}
EXTRACTABLE_FIELDS: tuple[str, ...] = tuple(FIELD_WIRE)

INTENTS: tuple[str, ...] = ("accept", "reject", "counter", "price_question", "question", "defer", "stop")
FLAGS: tuple[str, ...] = (
    "legal",
    "deceased_estate",
    "distress",
    "minor_or_no_authority",
    "human_question",
    "hostile",
)
ESCALATE_REASONS: tuple[str, ...] = FLAGS + ("human_requested", "cannot_respond_safely", "other")

# --- JSON schemas (structured outputs) ---------------------------------------------------------

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "description": "Vehicle facts the seller states in THIS message, in the wire format given.",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": list(EXTRACTABLE_FIELDS)},
                    "value": {"type": "string"},
                    "confidence": {
                        "type": "number",
                        "description": "0 to 1. Use 1.0 for a plain statement, ~0.7 for 'about'/'roughly', lower if unsure.",
                    },
                },
                "required": ["field", "value", "confidence"],
                "additionalProperties": False,
            },
        },
        "answered_pending": {
            "type": "boolean",
            "description": "True if the message answers the question the assistant last asked.",
        },
        "intents": {"type": "array", "items": {"type": "string", "enum": list(INTENTS)}},
        "flags": {"type": "array", "items": {"type": "string", "enum": list(FLAGS)}},
        "counter_price_aud": {
            "type": ["integer", "null"],
            "description": "A dollar amount the seller names as what they want, if any.",
        },
        "phone": {"type": ["string", "null"], "description": "A phone number the seller gives, if any."},
        "seller_question": {
            "type": ["string", "null"],
            "description": "A question the seller asks the assistant, paraphrased briefly, if any.",
        },
        "notes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Anything about the car or the sale the seller mentions that is not a field above.",
        },
    },
    "required": [
        "facts",
        "answered_pending",
        "intents",
        "flags",
        "counter_price_aud",
        "phone",
        "seller_question",
        "notes",
    ],
    "additionalProperties": False,
}

GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "description": "The text to send to the seller."},
        "proposed_state": {
            "type": "string",
            "enum": [s.value for s in LeadState],
            "description": "Your read of the conversation stage. Advisory only — the system decides.",
        },
        "confidence": {
            "type": "number",
            "description": "0 to 1: how well this message fits the instruction.",
        },
        "escalate": {
            "type": "boolean",
            "description": "True only if a person must take over instead of sending this message.",
        },
        "escalate_reason": {
            "anyOf": [{"type": "string", "enum": list(ESCALATE_REASONS)}, {"type": "null"}],
        },
    },
    "required": ["message", "proposed_state", "confidence", "escalate", "escalate_reason"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

# --- parsed outputs ----------------------------------------------------------------------------


@dataclass
class ExtractedFact:
    field: str
    value: Any  # coerced, fact-store shape
    confidence: float
    raw: str


@dataclass
class ExtractionOutput:
    facts: list[ExtractedFact] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(
        default_factory=list
    )  # values that failed coercion (for the trace)
    answered_pending: bool = False
    intents: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    counter_price_aud: int | None = None
    phone: str | None = None
    seller_question: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class GenerationOutput:
    message: str
    proposed_state: str | None
    confidence: float
    escalate: bool
    escalate_reason: str | None


def _enum_set(values: Any, allowed: tuple[str, ...]) -> set[str]:
    out: set[str] = set()
    if not isinstance(values, list):
        return out
    lookup = {a.lower(): a for a in allowed}
    for v in values:
        if isinstance(v, str) and v.strip().lower() in lookup:
            out.add(lookup[v.strip().lower()])
    return out


def _clean_str(v: Any, limit: int = 300) -> str | None:
    if not isinstance(v, str):
        return None
    s = " ".join(v.split())
    return s[:limit] if s else None


def parse_extraction(data: dict[str, Any] | None, *, today: date | None = None) -> ExtractionOutput | None:
    """Validate and coerce the extraction model's JSON. None if it is not usable at all."""
    if not isinstance(data, dict):
        return None
    out = ExtractionOutput()
    for item in data.get("facts") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("field", "")).strip().lower()
        raw = item.get("value")
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        if key not in FIELD_WIRE or not isinstance(raw, str):
            out.rejected.append({"field": key, "value": raw, "reason": "unknown field or non-string value"})
            continue
        coerced = coerce_fact(key, raw, today=today)
        if coerced is None:
            out.rejected.append({"field": key, "value": raw, "reason": "not in the wire format"})
            continue
        k, v = coerced
        out.facts.append(ExtractedFact(field=k, value=v, confidence=conf, raw=raw))
    out.answered_pending = bool(data.get("answered_pending", False))
    out.intents = _enum_set(data.get("intents"), INTENTS)
    out.flags = _enum_set(data.get("flags"), FLAGS)
    cp = data.get("counter_price_aud")
    if isinstance(cp, (int, float)) and not isinstance(cp, bool) and 500 <= cp <= 2_000_000:
        out.counter_price_aud = int(cp)
    phone = _clean_str(data.get("phone"), 40)
    if phone and (m := PHONE.search(phone)):
        digits = re.sub(r"\D", "", m.group(0))
        out.phone = "+61" + digits[-9:]
    out.seller_question = _clean_str(data.get("seller_question"), 200)
    notes = data.get("notes") or []
    out.notes = [n for n in (_clean_str(x, 160) for x in notes if isinstance(x, str)) if n][:6]
    return out


def parse_generation(data: dict[str, Any] | None) -> GenerationOutput | None:
    if not isinstance(data, dict):
        return None
    message = data.get("message")
    if not isinstance(message, str):
        return None
    state = data.get("proposed_state")
    states = {s.value: s.value for s in LeadState}
    proposed = states.get(str(state).upper()) if isinstance(state, str) else None
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        conf = 0.0
    reason = data.get("escalate_reason")
    lookup = {r.lower(): r for r in ESCALATE_REASONS}
    reason = lookup.get(reason.strip().lower()) if isinstance(reason, str) else None
    escalate = bool(data.get("escalate", False))
    return GenerationOutput(
        message=message.strip(),
        proposed_state=proposed,
        confidence=conf,
        escalate=escalate,
        escalate_reason=reason if escalate else None,
    )


# --- coercion into the fact vocabulary ---------------------------------------------------------

_ENUMS: dict[str, tuple[str, ...]] = {
    "service_history": ("full", "partial", "none"),
    "panel_paint_condition": ("excellent", "good", "fair", "poor"),
    "tyre_condition": ("new", "good", "worn", "replace"),
}
_NONE_WORDS = {"none", "no", "nil", "nothing", "n/a", "na", "false", "not"}
_YES_WORDS = {"yes", "true", "owing", "written_off", "written off", "writtenoff"}


def _split_tag(raw: str) -> tuple[str, str | None]:
    head, _, tail = raw.strip().partition(":")
    return head.strip().lower(), (tail.strip() or None)


def _digits(s: str | None) -> int | None:
    if not s:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    try:
        val = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    if re.search(r"\d\s?k\b", s.lower()) and val < 1000:
        val *= 1000
    return int(val)


def coerce_fact(key: str, raw: str, *, today: date | None = None) -> tuple[str, Any] | None:
    """Wire-format string → (field, fact-store value). None when the value cannot be trusted."""
    s = raw.strip()
    if not s:
        return None
    low = s.lower()

    if key in _ENUMS:
        for choice in _ENUMS[key]:
            if low == choice or low.startswith(choice + " ") or low.startswith(choice + ","):
                return key, choice
        return None

    if key == "odometer_km":
        val = parse_odometer(s) if not s.isdigit() else int(s)
        return (key, val) if val is not None and 500 <= val <= 1_500_000 else None

    if key == "keys_count":
        m = re.search(r"\d+", s)
        if not m:
            words = {"one": 1, "two": 2, "three": 3, "four": 4, "single": 1}
            return (key, words[low]) if low in words else None
        n = int(m.group(0))
        return (key, n) if 0 <= n <= 6 else None

    if key == "year":
        m = re.search(r"\b(19[89]\d|20[0-4]\d)\b", s)
        if not m:
            return None
        year = int(m.group(1))
        limit = (today or date.today()).year + 1
        return (key, year) if 1980 <= year <= limit else None

    if key == "variant":
        v = " ".join(s.split())
        return (key, v[:40]) if 1 <= len(v) <= 40 else None

    if key == "vin":
        v = re.sub(r"[^A-Z0-9]", "", s.upper())
        return (key, v) if VIN_RE.fullmatch(v) else None

    if key == "rego":
        ids = parse_identifier(s)
        if "vin" in ids:
            return "vin", ids["vin"]
        if "rego" in ids:
            return "rego", ids["rego"]
        return None

    if key == "finance_owing":
        head, tail = _split_tag(low)
        if head in _NONE_WORDS or head.startswith("no ") or head.startswith("none"):
            return key, {"owing": False, "amount_aud": None}
        if head in _YES_WORDS or head.startswith("owing") or head.startswith("yes"):
            amount = _digits(tail)
            return key, {"owing": True, "amount_aud": float(amount) if amount else None}
        return None

    if key == "write_off_status":
        head, tail = _split_tag(low)
        if (
            head in _NONE_WORDS
            or head.startswith("no ")
            or head.startswith("none")
            or head.startswith("never")
        ):
            return key, {"written_off": False, "type": None}
        if head in _YES_WORDS or head.startswith("written") or head.startswith("yes"):
            wtype = re.sub(r"[^a-z ]", "", tail.lower()).strip()[:32] if tail else None
            return key, {"written_off": True, "type": wtype or None}
        return None

    if key == "mechanical_faults":
        if low in _NONE_WORDS or re.fullmatch(r"(none|no faults?|no issues?|nothing)[.!]?", low):
            return key, {"none": True}
        items = [x.strip(" .") for x in re.split(r";|\n", s) if x.strip(" .")]
        if not items:
            return None
        lights = any(re.search(r"\b(light|lamp)\b", i.lower()) for i in items)
        return key, {"items": [i[:80] for i in items[:6]], "warning_lights": lights}

    if key == "rego_status":
        head, tail = _split_tag(low)
        if head.startswith("unreg") or head.startswith("not reg"):
            return key, {"status": "unregistered", "expiry": None}
        if head.startswith("exp") or head.startswith("lapsed"):
            return key, {"status": "expired", "expiry": None}
        if head.startswith("current") or head in {"yes", "registered", "valid"}:
            expiry = None
            if tail and (m := re.search(r"(20\d{2})-(\d{1,2})", tail)):
                y, mo = int(m.group(1)), int(m.group(2))
                if 1 <= mo <= 12:
                    expiry = date(y, mo, 1).isoformat()
            return key, {"status": "current", "expiry": expiry}
        return None

    return None
