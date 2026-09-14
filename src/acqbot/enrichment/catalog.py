"""A small vehicle catalog used by the lead simulator and the stub providers.

Approximate Australian new-vehicle prices for common private-sale models. This is stub data for
development; the real guide and auction providers replace it entirely.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    make: str
    model: str
    body: str
    new_price_aud: int
    variants: tuple[str, ...]
    segment: str  # small | medium | large | suv | ute | prestige


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry("Toyota", "Corolla", "hatch", 32_000, ("Ascent Sport", "SX", "ZR"), "small"),
    CatalogEntry("Toyota", "Camry", "sedan", 42_000, ("Ascent", "Ascent Sport", "SL"), "medium"),
    CatalogEntry("Toyota", "RAV4", "suv", 45_000, ("GX", "GXL", "Cruiser", "Edge"), "suv"),
    CatalogEntry("Toyota", "Kluger", "suv", 58_000, ("GX", "GXL", "Grande"), "suv"),
    CatalogEntry("Toyota", "Hilux", "ute", 55_000, ("Workmate", "SR", "SR5", "Rogue"), "ute"),
    CatalogEntry("Toyota", "Landcruiser Prado", "suv", 72_000, ("GX", "GXL", "VX", "Kakadu"), "suv"),
    CatalogEntry("Mazda", "3", "hatch", 33_000, ("G20 Pure", "G20 Evolve", "G25 GT"), "small"),
    CatalogEntry("Mazda", "CX-5", "suv", 42_000, ("Maxx", "Maxx Sport", "Touring", "GT"), "suv"),
    CatalogEntry("Mazda", "BT-50", "ute", 52_000, ("XT", "XTR", "GT"), "ute"),
    CatalogEntry("Hyundai", "i30", "hatch", 30_000, ("Active", "Elite", "N Line"), "small"),
    CatalogEntry("Hyundai", "Tucson", "suv", 40_000, ("Active", "Elite", "Highlander"), "suv"),
    CatalogEntry("Kia", "Cerato", "sedan", 29_000, ("S", "Sport", "GT"), "small"),
    CatalogEntry("Kia", "Sportage", "suv", 39_000, ("S", "SX", "GT-Line"), "suv"),
    CatalogEntry("Ford", "Ranger", "ute", 58_000, ("XL", "XLS", "XLT", "Wildtrak"), "ute"),
    CatalogEntry("Ford", "Focus", "hatch", 29_000, ("Trend", "Titanium", "ST-Line"), "small"),
    CatalogEntry("Volkswagen", "Golf", "hatch", 36_000, ("110TSI", "110TSI Life", "R-Line"), "small"),
    CatalogEntry("Volkswagen", "Tiguan", "suv", 47_000, ("110TSI", "132TSI", "162TSI R-Line"), "suv"),
    CatalogEntry("Subaru", "Forester", "suv", 41_000, ("2.5i", "2.5i-L", "2.5i-S"), "suv"),
    CatalogEntry("Subaru", "Outback", "wagon", 45_000, ("AWD", "AWD Sport", "AWD Touring"), "medium"),
    CatalogEntry("Mitsubishi", "Triton", "ute", 48_000, ("GLX", "GLX+", "GLS"), "ute"),
    CatalogEntry("Mitsubishi", "Outlander", "suv", 40_000, ("ES", "LS", "Exceed"), "suv"),
    CatalogEntry("Nissan", "X-Trail", "suv", 40_000, ("ST", "ST-L", "Ti"), "suv"),
    CatalogEntry("Nissan", "Navara", "ute", 50_000, ("SL", "ST", "ST-X"), "ute"),
    CatalogEntry("Holden", "Commodore", "sedan", 40_000, ("Evoke", "SV6", "SS"), "large"),
    CatalogEntry("Honda", "Civic", "hatch", 34_000, ("VTi", "VTi-S", "VTi-L"), "small"),
    CatalogEntry("Honda", "CR-V", "suv", 42_000, ("Vi", "VTi", "VTi-L"), "suv"),
    CatalogEntry("BMW", "3 Series", "sedan", 75_000, ("320i", "330i", "M340i"), "prestige"),
    CatalogEntry("Mercedes-Benz", "C-Class", "sedan", 80_000, ("C200", "C300", "C43"), "prestige"),
    CatalogEntry("Audi", "A4", "sedan", 70_000, ("35 TFSI", "40 TFSI", "45 TFSI"), "prestige"),
    CatalogEntry("Tesla", "Model 3", "sedan", 65_000, ("RWD", "Long Range", "Performance"), "prestige"),
)

DEFAULT_NEW_PRICE = 38_000


def find(make: str, model: str) -> CatalogEntry | None:
    from acqbot.ingestion.fingerprint import normalise_make, normalise_model

    nm, nmo = normalise_make(make), normalise_model(model)
    for e in CATALOG:
        if normalise_make(e.make) == nm and normalise_model(e.model) == nmo:
            return e
    return None


def segment_for(make: str, model: str) -> str:
    e = find(make, model)
    return e.segment if e else "medium"
