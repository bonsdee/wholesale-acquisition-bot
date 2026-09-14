"""Vehicle fingerprint for deduplication (Section 3.2).

The fingerprint hashes normalised make / model / year. Odometer is deliberately kept out of the hash
and applied as a tolerance in the dedupe query instead — a bucketed odometer creates boundary cases
(84,900 km and 85,100 km hashing differently) that defeat the purpose.
"""

from __future__ import annotations

import hashlib
import re

# Listing-text variations that should collapse to one make.
_MAKE_ALIASES = {
    "vw": "volkswagen",
    "merc": "mercedes",
    "mercedesbenz": "mercedes",
    "mercedesamg": "mercedes",
    "landrover": "landrover",
    "rangerover": "landrover",
    "chevy": "chevrolet",
    "holdenspecialvehicles": "hsv",
}

# Model spellings that should collapse.
_MODEL_ALIASES = {
    "landcruiser": "landcruiser",
    "landcruiserprado": "prado",
    "prado": "prado",
    "corollahatch": "corolla",
    "corollasedan": "corolla",
    "mazda3": "3",
    "mazda2": "2",
    "mazda6": "6",
    "cx5": "cx5",
    "cx-5": "cx5",
    "i30n": "i30",
    "hiluxsr5": "hilux",
    "hiluxsr": "hilux",
    "rangerwildtrak": "ranger",
    "rangerxlt": "ranger",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def normalise_make(make: str) -> str:
    n = _norm(make)
    return _MAKE_ALIASES.get(n, n)


def normalise_model(model: str) -> str:
    n = _norm(model)
    return _MODEL_ALIASES.get(n, n)


def vehicle_fingerprint(make: str, model: str, year: int) -> str:
    key = f"{normalise_make(make)}|{normalise_model(model)}|{int(year)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def odometer_matches(a_km: int, b_km: int, tolerance: float = 0.10, floor_km: int = 3_000) -> bool:
    """Two listings describe the same odometer if within 10% or 3,000 km, whichever is larger."""
    return abs(a_km - b_km) <= max(floor_km, tolerance * max(a_km, b_km))
