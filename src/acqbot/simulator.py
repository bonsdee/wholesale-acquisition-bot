"""Lead simulator — stands in for the upstream sourcing platform until it exists.

Generates Figure 2 payloads with a realistic spread of Melbourne private-sale vehicles. Scenarios
use the stub providers' magic suffixes (see enrichment/stubs.py) so each one enriches predictably:

    clean          everything checks out
    encumbered     PPSR shows finance owing
    written-off    PPSR shows a repairable write-off → lead terminates before contact
    stolen         PPSR shows stolen → lead terminates before contact
    contradiction  VIN decode returns a different year to the listing
    no-identifiers listing has neither VIN nor rego → PPSR deferred to DISCOVERY
    high-value     asking price above the escalation threshold
    expired-rego   rego lookup returns expired
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from acqbot.enrichment import catalog
from acqbot.enrichment.stubs import stub_trade_mid, synthetic_vin

SCENARIOS = (
    "clean",
    "encumbered",
    "written-off",
    "stolen",
    "contradiction",
    "no-identifiers",
    "high-value",
    "expired-rego",
)

_SUBURBS = [
    ("Southbank", "3006"),
    ("Port Melbourne", "3207"),
    ("Docklands", "3008"),
    ("Richmond", "3121"),
    ("Brunswick", "3056"),
    ("Footscray", "3011"),
    ("Preston", "3072"),
    ("Box Hill", "3128"),
    ("Dandenong", "3175"),
    ("Frankston", "3199"),
    ("Werribee", "3030"),
    ("Craigieburn", "3064"),
    ("Glen Waverley", "3150"),
    ("St Kilda", "3182"),
    ("Sunshine", "3020"),
    ("Reservoir", "3073"),
]
_FIRST = ["Sam", "Priya", "Jack", "Mei", "Liam", "Aisha", "Noah", "Elena", "Tom", "Hannah", "Arjun", "Chloe"]
_LAST = ["Nguyen", "Smith", "Patel", "Chen", "Williams", "Kaur", "Brown", "Rossi", "Taylor", "Ali", "Jones"]
_DESCRIPTIONS = [
    "Selling my {year} {make} {model}. Great car, always serviced. {km}km. Rego till {rego_month}. No time wasters.",
    "{year} {make} {model} {variant}. Reluctant sale, moving overseas. Full logbook. {km} kms. Two keys.",
    "{make} {model} {year} for sale. Drives perfect, a few small scratches. {km}km. Price negotiable.",
    "Upgrading so selling the {model}. {year} model, {km}km, new tyres last year. RWC available.",
]


def _plate(rng: random.Random, *, expired: bool = False, unregistered: bool = False) -> str:
    letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
    if expired:
        return f"{rng.randint(1, 9)}{rng.choice(letters)}{rng.choice(letters)}99{rng.choice(letters)}"
    if unregistered:
        return f"{rng.choice(letters)}{rng.choice(letters)}{rng.choice(letters)}00{rng.randint(1, 9)}"
    if rng.random() < 0.5:  # current VIC format 1AB2CD
        return (
            f"{rng.randint(1, 9)}{rng.choice(letters)}{rng.choice(letters)}"
            f"{rng.randint(1, 9)}{rng.choice(letters)}{rng.choice(letters)}"
        )
    return f"{rng.choice(letters)}{rng.choice(letters)}{rng.choice(letters)}{rng.randint(100, 999)}"


def make_lead(
    scenario: str = "clean", *, seed: int | None = None, rng: random.Random | None = None
) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {SCENARIOS}")
    rng = rng or random.Random(seed)
    entry = rng.choice(catalog.CATALOG)
    if scenario == "high-value":
        entry = rng.choice([e for e in catalog.CATALOG if e.segment == "prestige"])
    this_year = datetime.now(UTC).year
    year = rng.randint(this_year - 12, this_year - 1)
    if scenario == "high-value":
        year = rng.randint(this_year - 2, this_year)
    age = max(1, this_year - year)
    odometer = int(max(5_000, rng.gauss(age * 15_000, age * 4_000)))
    variant = rng.choice(entry.variants)
    trade_mid = stub_trade_mid(entry.make, entry.model, year, odometer)
    asking = int(round(trade_mid * rng.uniform(1.10, 1.30), -2))
    if scenario == "high-value":
        asking = max(asking, 65_000 + rng.randint(0, 40_000))

    lead_id = uuid.uuid4()
    vin: str | None = synthetic_vin(str(lead_id))
    suffix = {"encumbered": "E", "written-off": "W", "stolen": "S", "contradiction": "C"}.get(scenario)
    if suffix:
        vin = vin[:-1] + suffix
    elif vin[-1] in "EWSC":
        vin = vin[:-1] + "X"

    rego: str | None = _plate(rng, expired=(scenario == "expired-rego"))
    if scenario == "no-identifiers":
        vin, rego = None, None
    elif scenario == "clean" and rng.random() < 0.6:
        vin = None  # most listings show a plate but not a VIN

    name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
    suburb, postcode = rng.choice(_SUBURBS)
    rego_month = (datetime.now(UTC) + timedelta(days=rng.randint(30, 330))).strftime("%b %Y")
    desc = rng.choice(_DESCRIPTIONS).format(
        year=year,
        make=entry.make,
        model=entry.model,
        variant=variant,
        km=f"{odometer:,}",
        rego_month=rego_month,
    )
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "lead_id": str(lead_id),
        "source": "fb_marketplace",
        "listing_url": f"https://www.facebook.com/marketplace/item/{rng.randint(10**14, 10**15 - 1)}/",
        "seller": {
            "platform_id": f"sim-{rng.randint(10**8, 10**9 - 1)}",
            "display_name": name,
            "phone": f"04{rng.randint(10_000_000, 99_999_999)}" if rng.random() < 0.3 else None,
        },
        "vehicle_claimed": {
            "make": entry.make,
            "model": entry.model,
            "variant": variant if rng.random() < 0.7 else None,
            "year": year,
            "odometer_km": odometer,
            "rego": rego,
            "vin": vin,
            "transmission": "manual" if entry.segment in {"small", "ute"} and rng.random() < 0.2 else "auto",
            "fuel": "ev" if entry.make == "Tesla" else ("diesel" if entry.segment == "ute" else "petrol"),
            "asking_price_aud": asking,
            "description_raw": desc,
            "images": [
                f"https://example.invalid/listing/{lead_id}/{i}.jpg" for i in range(rng.randint(3, 10))
            ],
        },
        "location": {"suburb": suburb, "state": "VIC", "postcode": postcode},
        "qualified_at": (datetime.now(UTC) - timedelta(minutes=rng.randint(1, 240))).isoformat(),
        "qualification_notes": f"simulated lead — scenario {scenario}",
    }
    return payload


def make_leads(count: int, *, seed: int | None = None, scenario: str | None = None) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    weights = {
        "clean": 70,
        "encumbered": 8,
        "written-off": 3,
        "stolen": 1,
        "contradiction": 6,
        "no-identifiers": 6,
        "high-value": 3,
        "expired-rego": 3,
    }
    out = []
    for _ in range(count):
        sc = scenario or rng.choices(list(weights), weights=list(weights.values()), k=1)[0]
        out.append(make_lead(sc, rng=rng))
    return out
