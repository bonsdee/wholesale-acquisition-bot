"""Rule-based extraction for the scripted (no-model) conversation — Phase 3.

The templates ask one field at a time in a constrained form, so a small set of parsers covers the
answers. Phase 4 swaps this for the extraction model behind the same `Extraction` result; the
escalation keyword screens stay in place as a floor underneath the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# --- escalation screens (Section 5.2) -------------------------------------------------------

LEGAL = re.compile(
    r"\b(lawyer|solicitor|legal action|ombudsman|consumer affairs|vcat|accc|dispute|sue you|suing|fair trading)\b",
    re.I,
)
DECEASED = re.compile(
    r"\b(deceased|passed away|passed on|estate of|executor|probate|late (husband|wife|father|mother|partner))\b",
    re.I,
)
DISTRESS = re.compile(
    r"\b(desperate|can'?t cope|hospital|funeral|eviction|evicted|breaking down|need the money (today|now)|"
    r"about to lose|repossess|repo man|no other option)\b",
    re.I,
)
# Whose car is it. The commonest real phrasing is not "I am not the owner" — it is "it's my mum's
# car", which this missed until the review harness ran a seller who said exactly that.
_RELATIVE = (
    r"mum|mom|mother|dad|father|nan|nana|nanna|grandma|grandmother|grandad|grandpa|grandfather|"
    r"partner|husband|wife|brother|sister|son|daughter|uncle|aunt|aunty|cousin|mate|friend|"
    r"neighbour|neighbor|boss|landlord"
)
MINOR_OR_NO_AUTHORITY = re.compile(
    r"\b(under 18|i'?m 1[0-7]\b|i am 1[0-7]\b|not my car|isn'?t my car|not the owner|not in my name|"
    rf"in my ({_RELATIVE})'?s name|"
    rf"my ({_RELATIVE})'?s (car|vehicle|ute|van|suv|wagon)|"
    rf"(car|vehicle|ute|van) (is|belongs to) my ({_RELATIVE})|"
    rf"selling (it |the car )?(for|on behalf of) (my|a|an) ({_RELATIVE})|"
    r"on behalf of the owner|power of attorney|"
    rf"handling the sale for (my|her|his|their|them|a|an)( ({_RELATIVE}))?)\b",
    re.I,
)
HUMAN_QUESTION = re.compile(
    r"(\b(are|am i talking to|am i speaking to|is this|r u|you a|you an|this a)\b[^.?!]{0,40}\b(bot|robot|real person|human|automated|ai|chatbot|machine|computer)\b)"
    r"|(\b(bot|robot|chatbot)\b\s*\?)",
    re.I,
)
HOSTILE = re.compile(
    r"\b(f+u+c+k|fucking|shit|piss off|scam(mer)?s?|bullshit|stop messaging|leave me alone|waste of (my )?time|"
    r"get lost|idiot|moron|f off|screw you)\b",
    re.I,
)
STOP = re.compile(r"^\s*(stop|unsubscribe|opt out|no more messages|don'?t contact me)\s*[.!]?\s*$", re.I)

# --- intents ---------------------------------------------------------------------------------

# Acceptance is anchored at the start so "no deal" cannot match, but a real seller puts a word in
# front of it — "Alright, done" — so an optional filler is allowed before the word that decides.
_ACCEPT_FILLER = (
    r"(?:(?:alright|all right|righto?|right|well|great|perfect|awesome|cool|sweet|lovely|nice|"
    r"ok(?:ay)?|yeah|yep|sure|fine|go on then|happy with that)[,!.\s]+)*"
)
ACCEPT = re.compile(
    rf"^\s*{_ACCEPT_FILLER}"
    r"(yes|yep|yeah|yup|ok(ay)?|deal|done|accept(ed)?|i'?ll take it|let'?s do it|sounds good|"
    r"that works|that'?ll do|agreed|sold|happy with that|works for me)\b",
    re.I,
)
REJECT = re.compile(
    # "yeah nah" is a no. Without it the leading "yeah" reads as an acceptance.
    r"\b(no thanks|not interested|i'?ll pass|pass on|too low|way too low|not enough|no way|forget it|"
    r"insulting|lowball|not selling for that|keep it|yeah,? nah|nah,? (mate|sorry|i'?m right))\b",
    re.I,
)
PUSHBACK = re.compile(
    r"\b(do (a bit |a little |any |much )?better|(bit|little|any) more|come up( a bit)?|go higher|any higher|sharpen|"
    r"best you can do|is that (the )?best|that'?s it\?|any movement|wiggle room|negotiable|meet me|split the difference|"
    r"was hoping for more|hoping for (a bit |a little )?more|expecting more|closer to)\b",
    re.I,
)
PRICE_QUESTION = re.compile(
    r"\b(how much|what('?s| is| would) (your|the) (offer|price|number)|what (would|will|can) you (pay|offer|give)|make me an offer|best price)\b",
    re.I,
)
MONEY = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+|\d{3,6})(?:\s?k\b)?|\b(\d{1,3}(?:,\d{3})+|\d{4,6})\s?(?:dollars|bucks|aud)\b|\b(\d{1,3}(?:\.\d)?)\s?k\b",
    re.I,
)
PHONE = re.compile(r"(?<!\d)(\+?61\s?4\d{2}|04\d{2})[\s-]?\d{3}[\s-]?\d{3}(?!\d)")

# --- field parsers ---------------------------------------------------------------------------

VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
REGO_RE = re.compile(r"\b(\d[A-Z]{2}\d[A-Z]{2}|[A-Z]{3}\d{3}|[A-Z]{2,3}\d{2,3}[A-Z]?|\d{3}[A-Z]{3})\b")
ODO_RE = re.compile(
    r"\b(\d{1,3}(?:[,\s]\d{3})+|\d{4,6}|\d{1,3}(?:\.\d)?\s?k)\b\s*(?:km|kms|kilometres|kilometers)?", re.I
)
KEYS_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "single": 1, "1": 1, "2": 2, "3": 3, "4": 4}
MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
NEGATION = re.compile(
    r"\b(no|never|not|none|nil|nope|nothing|isn'?t|hasn'?t|wasn'?t|haven'?t|paid (it )?off|owned outright|outright)\b",
    re.I,
)


@dataclass
class Extraction:
    facts: dict[str, Any] = field(default_factory=dict)
    photo_urls: list[str] = field(default_factory=list)
    flags: set[str] = field(
        default_factory=set
    )  # legal, deceased, distress, minor_or_no_authority, human_question, hostile, stop
    intents: set[str] = field(default_factory=set)  # accept, reject, counter, price_question, question, defer
    counter_price: int | None = None
    phone: str | None = None
    parsed_pending: bool = False  # the field we asked for was understood
    # Phase 4 additions — populated by the model extractor, empty on the rule-based path.
    seller_question: str | None = None
    notes: list[str] = field(default_factory=list)
    low_confidence: dict[str, Any] = field(default_factory=dict)  # field → raw value we chose not to record
    generator: str = "rules"  # rules | model | rules_fallback (model call failed)
    model_call_id: str | None = None


def _money_to_int(m: re.Match) -> int | None:
    raw = next((g for g in m.groups() if g), None)
    if raw is None:
        return None
    text = m.group(0).lower()
    val = float(raw.replace(",", ""))
    if "k" in text and val < 1000:
        val *= 1000
    return int(val)


def parse_odometer(text: str) -> int | None:
    m = ODO_RE.search(text.replace(",", ",") if text else "")
    if not m:
        return None
    raw = m.group(1).lower().replace(",", "").replace(" ", "")
    if raw.endswith("k"):
        return int(float(raw[:-1]) * 1000)
    val = int(raw)
    return val if 500 <= val <= 1_500_000 else None


def parse_service_history(text: str) -> str | None:
    t = text.lower()
    if re.search(
        r"\b(full|complete|every service|all (the )?services|logbook(s)? (are )?(all )?(stamped|complete|up to date))\b",
        t,
    ) and not re.search(r"\b(not|no|partial|missing|lost)\b", t):
        return "full"
    if re.search(r"\b(partial|some|most|a few|patchy|missing (a )?(few|couple)|incomplete|half)\b", t):
        return "partial"
    if re.search(
        r"\b(none|no (service )?history|no logbook|lost (it|the book|the logbook)|nothing|never serviced|no records)\b",
        t,
    ):
        return "none"
    if re.fullmatch(r"\s*full\s*\.?", t):
        return "full"
    return None


def parse_yes_no_claim(text: str) -> bool | None:
    """For finance owing / write-off: True = yes there is, False = no there isn't."""
    t = text.lower().strip()
    if re.search(
        r"\b(yes|yeah|yep|there is|still (owe|owing|paying)|under finance|on finance|finance (owing|on it)|owe|loan on it|written off|repairable|hail damage|was written)\b",
        t,
    ) and not NEGATION.search(t):
        return True
    if NEGATION.search(t) or re.fullmatch(r"(no|nope|nah)[.!]?", t):
        return False
    return None


def parse_panel(text: str) -> str | None:
    """Grade panel and paint.

    The question offers four grades, so an explicit grade word is the answer and wins outright —
    the damage the seller volunteers alongside it ("good, couple of scratches on the rear bumper")
    is colour, not a contradiction. Only when no grade is stated do we infer one from the damage
    described, and there a body-part noun on its own ("bumper", "door") says nothing about
    condition; it needs an actual damage word next to it.
    """
    t = text.lower()
    if re.search(r"\b(excellent|immaculate|perfect|mint|flawless|like new|as new|showroom)\b", t):
        return "excellent"
    if re.search(r"\bpoor\b", t):
        return "poor"
    if re.search(r"\bfair\b", t):
        return "fair"
    if re.search(r"\b(good|fine|tidy|neat|presentable)\b", t):
        return "good"

    # No grade stated — infer from the damage described.
    if re.search(
        r"\b(rust|rusty|hail|major damage|rough|dented all over|needs (a )?respray|needs paint|peeling|panels? (are )?(bad|shot)|write[- ]?off)\b",
        t,
    ):
        return "poor"
    if re.search(
        r"\b(dents?|dinged|dings?|scuffed|scuffs?|scraped|scrapes?|cracked|chipped paint|some damage|a few marks|average|kerb(ed| rash))\b",
        t,
    ):
        return "fair"
    if re.search(
        r"\b(minor|small scratch|light scratch|few scratches|couple of scratches|stone chips?|normal wear|nothing major|clean)\b",
        t,
    ):
        return "good"
    return None


def parse_mechanical(text: str) -> dict[str, Any] | None:
    t = text.lower().strip()
    if re.fullmatch(
        r"(none|no|nope|nothing|nil|n/?a|all good|no faults|no issues|runs (great|fine|perfect(ly)?)|drives (great|fine|perfect(ly)?)|no lights)[.!]?",
        t,
    ) or (
        NEGATION.search(t)
        and not re.search(
            r"\b(light|leak|noise|fault|issue|problem|warning|abs|engine|gearbox|clutch|brake)\b", t
        )
    ):
        return {"none": True}
    if re.fullmatch(r"\s*", t):
        return None
    wl = bool(
        re.search(
            r"\b(warning light|check engine|engine light|abs light|airbag light|srs|dash light|light(s)? on)\b",
            t,
        )
    )
    items = [x.strip(" .") for x in re.split(r",|;| and |\n", t) if x.strip(" .")]
    return {"items": items[:6], "warning_lights": wl}


def parse_tyres(text: str) -> str | None:
    t = text.lower()
    if re.search(
        r"\b(bald|need(s)? replac(ing|ed)|replace|shot|worn out|illegal|cords showing|no tread)\b", t
    ):
        return "replace"
    if re.search(r"\b(new|brand new|just replaced|recently replaced|replaced last)\b", t):
        return "new"
    if re.search(r"\b(worn|getting low|half|50%|a bit low|wearing)\b", t):
        return "worn"
    if re.search(r"\b(good|fine|ok|okay|plenty|decent|heaps of tread|lots of tread)\b", t):
        return "good"
    return None


def parse_keys(text: str) -> int | None:
    t = text.lower()
    m = re.search(r"\b(one|two|three|four|single|[1-4])\b", t)
    if m:
        return KEYS_WORDS[m.group(1)]
    if re.search(r"\bjust the one\b|\bonly (got )?one\b", t):
        return 1
    return None


def parse_rego_status(text: str, *, today: date | None = None) -> dict[str, Any] | None:
    t = text.lower()
    today = today or date.today()
    if re.search(r"\b(unregistered|unreg|no rego|not registered|never registered)\b", t):
        return {"status": "unregistered", "expiry": None}
    if re.search(r"\b(expired|lapsed|ran out|out of rego)\b", t):
        return {"status": "expired", "expiry": None}
    expiry = None
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{4}|\d{2})?\b", t)
    if m:
        month = MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else today.year
        if year < 100:
            year += 2000
        if not m.group(2) and month < today.month:
            year += 1
        expiry = date(year, month, 1).isoformat()
    m2 = re.search(r"\b(\d{1,2})/(\d{2,4})\b", t)
    if m2 and expiry is None:
        month, year = int(m2.group(1)), int(m2.group(2))
        if 1 <= month <= 12:
            if year < 100:
                year += 2000
            expiry = date(year, month, 1).isoformat()
    if expiry or re.search(
        r"\b(current|yes|yep|registered|till|until|valid|good till|good until|paid up)\b", t
    ):
        return {"status": "current", "expiry": expiry}
    return None


STRICT_PLATE_RE = re.compile(r"\d[A-Z]{2}\d[A-Z]{2}|[A-Z]{3}\d{3}|\d{3}[A-Z]{3}")
PLATE_RE = re.compile(
    r"\d[A-Z]{2}\d[A-Z]{2}|[A-Z]{3}\d{3}|[A-Z]{2,3}\d{2,3}[A-Z]?|\d{3}[A-Z]{2,3}|[A-Z]{1,3}\d{1,4}[A-Z]{1,3}"
)
_NOT_PLATES = {
    "COVID19",
    "MK2",
    "MK3",
    "V6",
    "V8",
    "4WD",
    "2WD",
    "AWD",
    "SR5",
    "GT3",
    "RS4",
    "M3",
    "A4",
    "Q5",
    "X5",
    "CX5",
    "CX3",
    "CX9",
    "I30",
    "I20",
    "BT50",
}


def parse_identifier(text: str) -> dict[str, str]:
    """Find a VIN or a plate in free text. Plates may be written with a space or hyphen (1AB 2CD)."""
    out: dict[str, str] = {}
    tokens = re.findall(r"[A-Z0-9-]+", (text or "").upper())
    cleaned = [t.replace("-", "") for t in tokens]
    for t in cleaned:
        if len(t) == 17 and VIN_RE.fullmatch(t):
            out["vin"] = t
            break
    for c in cleaned:
        if c == out.get("vin") or c in _NOT_PLATES or c.isalpha() or c.isdigit():
            continue
        if 4 <= len(c) <= 7 and PLATE_RE.fullmatch(c):
            out["rego"] = c
            return out
    # Plates split by a space or hyphen: only the standard VIC shapes.
    for a, b in zip(cleaned, cleaned[1:], strict=False):
        c = a + b
        if len(c) == 6 and STRICT_PLATE_RE.fullmatch(c) and not (a.isalpha() and b.isalpha()):
            out["rego"] = c
            return out
    return out


PENDING_PARSERS = {
    "rego": lambda t: parse_identifier(t) or None,
    "odometer_km": parse_odometer,
    "service_history": parse_service_history,
    "finance_owing": lambda t: (
        lambda yn: None if yn is None else {"owing": yn, "amount_aud": _amount(t) if yn else None}
    )(parse_yes_no_claim(t)),
    "write_off_status": lambda t: (
        lambda yn: (
            None
            if yn is None
            else {"written_off": yn, "type": ("hail" if "hail" in t.lower() else None) if yn else None}
        )
    )(parse_yes_no_claim(t)),
    "panel_paint_condition": parse_panel,
    "mechanical_faults": parse_mechanical,
    "tyre_condition": parse_tyres,
    "keys_count": parse_keys,
    "rego_status": parse_rego_status,
}


def _amount(text: str) -> float | None:
    m = MONEY.search(text)
    return float(_money_to_int(m)) if m else None


def extract(
    body: str, attachments: list[dict[str, Any]], *, pending_field: str | None, stage_priced: bool
) -> Extraction:
    ex = Extraction()
    text = body or ""

    # Screens first — they decide whether anything else matters.
    if LEGAL.search(text):
        ex.flags.add("legal")
    if DECEASED.search(text):
        ex.flags.add("deceased_estate")
    if DISTRESS.search(text):
        ex.flags.add("distress")
    if MINOR_OR_NO_AUTHORITY.search(text):
        ex.flags.add("minor_or_no_authority")
    if HUMAN_QUESTION.search(text):
        ex.flags.add("human_question")
    if HOSTILE.search(text):
        ex.flags.add("hostile")
    if STOP.match(text):
        ex.flags.add("stop")

    ex.photo_urls = [a["url"] for a in attachments if a.get("type") == "image" and a.get("url")]
    if m := PHONE.search(text):
        digits = re.sub(r"\D", "", m.group(0))
        ex.phone = "+61" + digits[-9:]

    # Intents that matter once a price is on the table.
    if stage_priced:
        if ACCEPT.match(text) and not REJECT.search(text):
            ex.intents.add("accept")
        if REJECT.search(text):
            ex.intents.add("reject")
        if PUSHBACK.search(text):
            ex.intents.add("counter")
            ex.intents.discard("accept")
        if mm := MONEY.search(text):
            val = _money_to_int(mm)
            if val and val >= 500:
                ex.counter_price = val
                ex.intents.add("counter")
                ex.intents.discard("accept")
    if PRICE_QUESTION.search(text):
        ex.intents.add("price_question")

    # The field we asked for.
    if pending_field and pending_field in PENDING_PARSERS:
        val = PENDING_PARSERS[pending_field](text)
        if val is not None:
            if pending_field == "rego":
                ex.facts.update(val)
            else:
                ex.facts[pending_field] = val
            ex.parsed_pending = True

    # Volunteered identifiers are always worth taking.
    if "rego" not in ex.facts and "vin" not in ex.facts:
        ids = parse_identifier(text)
        if "vin" in ids:
            ex.facts["vin"] = ids["vin"]
        elif pending_field is None and "rego" in ids and len(text.split()) <= 3:
            ex.facts["rego"] = ids["rego"]
    return ex
