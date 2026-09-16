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
        ask="Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?",
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
        why="Six exterior angles, interior, dash — verifies claims",
        kind="list",
        verified_sources=("photo",),
        ask=(
            "Last thing — can you send photos: the six exterior angles (front, back, both sides, both front "
            "corners), the interior, and the dash with the ignition on so the odometer shows?"
        ),
        extra={"min_photos": 6},
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
    ask=(
        "What's the best mobile for you? Marketplace hides messages after a day or so and I don't "
        "want the offer sitting somewhere you won't see it."
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
