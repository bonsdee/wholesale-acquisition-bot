"""Field registry — the vocabulary of the fact store.

Section 5.1 lists the fields that gate pricing. Each one exists because it moves the price.
Confidence conventions for seller-sourced facts:
    0.6  taken from the listing text (never re-stated by the seller)
    0.9  stated by the seller in conversation ("confirmed")
    1.0  verified by an authoritative source (PPSR, VIN decode, rego lookup, photo, inspection)
"""

from __future__ import annotations

from dataclasses import dataclass, field

LISTING_CONFIDENCE = 0.6
STATED_CONFIDENCE = 0.9

# The photographs asked for, worded once, here. Five, to match the "at least 5 photos" on the
# dealership's own sell form (client, 21 Sep) — a seller who has been through the site has already
# taken them. Chosen for what they verify: the four sides are the panel-and-paint grade, and the
# dash with the ignition on is the odometer reading and any warning lights. The spec's interior and
# corner shots [5.1] were dropped because no priced field reads them. "Both sides" is two photos.
PHOTO_SHOTS = "the front, the back, both sides, and the dash with the ignition on so the odometer shows"
PHOTO_SHOTS_COUNT = 5


def photos_needed(need: int) -> str:
    """'5 in total: the front, …' — the count and the list, kept in agreement. At any other
    ACQBOT_MIN_PHOTOS the list becomes a guide, so the message never counts five things as eight."""
    if need == PHOTO_SHOTS_COUNT:
        return f"{need} in total: {PHOTO_SHOTS}"
    return f"{need} in total, ideally including {PHOTO_SHOTS}"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    why: str
    kind: str  # int | enum | text | bool | list | object
    choices: tuple[str, ...] = ()
    # DISCOVERY exit: fact present with at least this confidence (or verified).
    min_confidence: float = STATED_CONFIDENCE
    # VERIFICATION exit: must come from one of these sources, verified.
    verified_sources: tuple[str, ...] = ()
    # Free-text prompt fragment used by the scripted templates (Phase 3) and the model (Phase 4).
    ask: str = ""
    extra: dict = field(default_factory=dict)


# Fields required to exit DISCOVERY (Section 5.1), in the order the conversation asks for them.
DISCOVERY_REQUIRED: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="rego",
        label="Registration plate",
        why="Unlocks the PPSR and rego checks — nothing can be priced without an identifier",
        kind="text",
        min_confidence=STATED_CONFIDENCE,
        ask="What's the rego plate? (Or the VIN if it's handy — it's on the windscreen and the compliance plate.)",
    ),
    FieldSpec(
        key="odometer_km",
        label="Confirmed odometer",
        why="Primary value driver after model and year",
        kind="int",
        ask="What's the exact odometer reading right now, in km?",
    ),
    FieldSpec(
        key="service_history",
        label="Service history",
        why="Full logbook against none is a large valuation swing",
        kind="enum",
        choices=("full", "partial", "none"),
        ask="Service history — full logbook, partial, or none?",
    ),
    FieldSpec(
        key="finance_owing",
        label="Finance owing (PPSR)",
        why="Determines settlement mechanics and deal viability",
        kind="object",
        verified_sources=("ppsr",),
        ask="Is there any finance owing on it? (We run a PPSR check either way.)",
    ),
    FieldSpec(
        key="write_off_status",
        label="Write-off / hail status",
        why="Frequently a hard pass; always a major adjustment",
        kind="object",
        verified_sources=("ppsr",),
        ask="Has it ever been written off or had hail damage?",
    ),
    FieldSpec(
        key="panel_paint_condition",
        label="Panel and paint condition",
        why="Direct reconditioning cost line",
        kind="enum",
        choices=("excellent", "good", "fair", "poor"),
        # One question and nothing that can be turned into a second. Asked as two, a seller on a
        # phone answers whichever they read last — usually the damage — and the grade that drives
        # the recon estimate never arrives. The softer "mention any dents while you're there" was
        # no better: on the first live run the model turned it straight back into "Any dents or
        # scratches worth mentioning?". Volunteered damage is still read by the extractor.
        ask="How's the panel and paint — excellent, good, fair, or poor?",
    ),
    FieldSpec(
        key="mechanical_faults",
        label="Mechanical faults, warning lights",
        why="Direct reconditioning cost line",
        kind="object",
        ask="Any mechanical faults or warning lights on the dash? If none, just say none.",
    ),
    FieldSpec(
        key="tyre_condition",
        label="Tyre condition",
        why="Discrete reconditioning line item",
        kind="enum",
        choices=("new", "good", "worn", "replace"),
        ask="Tyres — new, good, worn, or need replacing?",
    ),
    FieldSpec(
        key="keys_count",
        label="Number of keys",
        why="Discrete reconditioning line item; replacements are costly",
        kind="int",
        ask="How many keys come with it?",
    ),
    FieldSpec(
        key="rego_status",
        label="Registration status and expiry",
        why="Affects saleability and holding cost",
        kind="object",
        verified_sources=("rego",),
        ask="Is the rego current, and when does it expire?",
    ),
    FieldSpec(
        key="photos",
        label="Photographs",
        why="Front, back, both sides and the dash — verifies the panel-and-paint grade and the odometer",
        kind="list",
        verified_sources=("photo",),
        # No count in the question: the list carries it, and a number here would contradict
        # ACQBOT_MIN_PHOTOS the day someone changes it. The chase-up (photos_needed) states one.
        ask=f"Last thing — can you send photos of {PHOTO_SHOTS}?",
    ),
)

DISCOVERY_REQUIRED_KEYS: tuple[str, ...] = tuple(f.key for f in DISCOVERY_REQUIRED)

# Asked during discovery but NOT gating (4.2 Stage 2). The mobile number is how the conversation
# survives the Messenger 24-hour window, so it matters — but a seller who will not give one can
# still be priced and still gets an offer, so it must never hold up DISCOVERY.
SELLER_PHONE = FieldSpec(
    key="seller_phone",
    label="Mobile number",
    why="Carries the conversation off Messenger when its 24-hour window shuts",
    kind="text",
    # The reason given has to be the TRUE one. This used to say "Marketplace hides messages after
    # a day or so", which it doesn't — the real limit is Meta's: a business may only reply for 24
    # hours after the seller's last message. A false reason given to get someone's phone number is
    # exactly what a privacy review (APP 3, collection by fair means) exists to catch.
    ask=(
        "What's the best mobile for you? Messenger only lets us reply for a day after your last "
        "message, and I don't want the offer to have no way of reaching you."
    ),
)
ASKED_NOT_GATING: tuple[FieldSpec, ...] = (SELLER_PHONE,)
VERIFICATION_REQUIRED: tuple[FieldSpec, ...] = tuple(f for f in DISCOVERY_REQUIRED if f.verified_sources)

# Facts recorded from the listing at ingestion (all seller assertions).
LISTING_FIELDS: tuple[str, ...] = (
    "make",
    "model",
    "variant",
    "year",
    "odometer_km",
    "rego",
    "vin",
    "transmission",
    "fuel",
    "asking_price_aud",
    "listing_images",
)

# Facts produced by enrichment providers.
ENRICHMENT_FIELDS: tuple[str, ...] = (
    "variant",
    "build_month",
    "body",
    "factory_options",
    "finance_owing",
    "write_off_status",
    "stolen",
    "rego_status",
    "vin",
)


def spec_for(key: str) -> FieldSpec | None:
    for f in DISCOVERY_REQUIRED + ASKED_NOT_GATING:
        if f.key == key:
            return f
    return None
