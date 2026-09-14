"""Valuation engine — Section 6. Pure computation: no database, no network, no language model.

    base_value     = blend(trade guide midpoint, recency- and match-weighted auction comps)
    condition_adj  = value-side deductions the market applies (service history, damage history,
                     write-off history, registration state, uncertainty for warning lights)
    market_adj     = days-supply, seasonality, our own stock position in the segment
    recon_estimate = cost-side line items to make the car retail-ready + contingency that scales
                     with how much of the condition has been verified visually vs asserted verbally
    wholesale_max  = base_value + condition_adj + market_adj - recon_estimate - target_margin - transport

Value-side and cost-side factors are kept apart so nothing is counted twice: worn tyres are a recon
line (we will pay to replace them), a missing logbook is a condition deduction (the market pays
less for the car and no amount of spend fixes it).

The engine emits `wholesale_max`, an uncertainty band around it, and the four-step ladder. Those
four ladder figures are the entire universe of numbers the conversation layer may ever express.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

ENGINE_VERSION = "2.0.0"


class InsufficientMarketData(ValueError):
    """Neither a guide range nor any comparables — the vehicle cannot be priced."""


# --------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------


@dataclass
class VehicleInputs:
    make: str
    model: str
    variant: str | None
    year: int
    odometer_km: int
    segment: str = "medium"  # small | medium | large | suv | ute | prestige
    service_history: str | None = None  # full | partial | none
    panel_paint_condition: str | None = None  # excellent | good | fair | poor
    mechanical_faults: dict[str, Any] | None = (
        None  # {"none": true} | {"items": [...], "warning_lights": bool}
    )
    tyre_condition: str | None = None  # new | good | worn | replace
    keys_count: int | None = None
    rego_status: dict[str, Any] | None = None  # {"status": current|expired|unregistered, "expiry": iso}
    finance_owing: dict[str, Any] | None = None  # {"owing": bool, "amount_aud": float}
    write_off_status: dict[str, Any] | None = None  # {"written_off": bool, "type": repairable|statutory}
    prior_damage: bool | None = None
    # Which of the fields above (plus identity fields) came from a verified source.
    verified_fields: set[str] = field(default_factory=set)


@dataclass
class CompInput:
    sold_at: date
    price_aud: float
    odometer_km: int
    match_quality: float = 1.0  # 0..1
    source: str = ""


@dataclass
class MarketInputs:
    guide_trade_low: float | None = None
    guide_trade_high: float | None = None
    guide_as_of: date | None = None
    comps: list[CompInput] = field(default_factory=list)
    days_supply: float | None = None  # market days' supply for the model, if known
    stock_position: str | None = None  # short | balanced | long — our stock in the segment
    valuation_date: date = field(default_factory=date.today)


@dataclass
class EngineConfig:
    target_margin_pct: float = 0.10
    target_margin_by_segment: dict[str, float] = field(default_factory=dict)
    transport_cost_aud: float = 250.0
    ladder_opening: float = 0.88
    ladder_step_1: float = 0.93
    ladder_step_2: float = 0.97
    ladder_rounding_aud: int = 50
    comps_half_life_days: float = 45.0
    comps_max_weight: float = 0.6
    km_adjust_per_10k: float = 0.015  # comps odometer normalisation
    contingency_floor: float = 0.05  # applies even when fully verified
    contingency_unverified: float = 0.20  # applies when nothing is verified visually
    seasonality: dict[str, dict[int, float]] = field(default_factory=dict)  # segment -> month -> factor

    def margin_for(self, segment: str) -> float:
        return float(self.target_margin_by_segment.get(segment, self.target_margin_pct))


# Cost-side tables (AUD). Calibrate against actual recon spend — Section 6.3.
TYRE_EACH_BY_SEGMENT = {"small": 160, "medium": 190, "large": 210, "suv": 230, "ute": 260, "prestige": 320}
KEY_REPLACEMENT_BY_SEGMENT = {
    "small": 350,
    "medium": 400,
    "large": 450,
    "suv": 450,
    "ute": 400,
    "prestige": 750,
}
PANEL_PAINT_RECON = {"excellent": 0, "good": 150, "fair": 650, "poor": 1_900}
DETAIL_AUD = 220
SAFETY_CHECK_AUD = 180
FULL_SERVICE_AUD = 420
RWC_AUD = 160
REGO_RENEWAL_AUD = 900
WARNING_LIGHT_DIAG_AUD = 250
MECHANICAL_FAULT_EACH_AUD = 650
# Expected recon spend when a condition field is still unknown: the distribution mean, not zero.
# Replaced by the real line the moment the fact is collected. Calibrate alongside the tables above.
EXPECTED_RECON_WHEN_UNKNOWN = {
    "panel_paint_condition": 420,
    "mechanical_faults": 260,
    "tyre_condition": 210,
    "keys_count": 130,
    "service_history": 190,
}

# Value-side deductions as a fraction of base value.
SERVICE_HISTORY_ADJ = {"full": 0.0, "partial": -0.025, "none": -0.06}
PRIOR_DAMAGE_ADJ = -0.04
WRITE_OFF_ADJ = -0.25
PANEL_POOR_STIGMA_ADJ = -0.02
WARNING_LIGHT_UNCERTAINTY_ADJ = -0.02
REGO_EXPIRED_ADJ = -0.01
REGO_UNREGISTERED_ADJ = -0.02

CONDITION_FIELDS = (
    "panel_paint_condition",
    "mechanical_faults",
    "tyre_condition",
    "keys_count",
    "service_history",
)


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------


@dataclass
class Line:
    code: str
    label: str
    amount: float
    verified: bool = False
    note: str = ""


@dataclass
class ValuationResult:
    engine_version: str
    base_value: float
    base_components: dict[str, Any]
    condition_adj: float
    condition_lines: list[Line]
    market_adj: float
    market_lines: list[Line]
    recon_lines: list[Line]
    recon_subtotal: float
    contingency: float
    contingency_rate: float
    verified_share: float
    recon_estimate: float
    target_margin: float
    transport_cost: float
    wholesale_max: float
    band_low: float
    band_high: float
    ladder: dict[str, float]
    warnings: list[str]
    inputs_snapshot: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------------------
# Computation
# --------------------------------------------------------------------------------------


def _round_money(x: float) -> float:
    return round(float(x), 2)


def _floor_to(x: float, step: int) -> float:
    return math.floor(x / step) * step


def compute_base_value(
    v: VehicleInputs, m: MarketInputs, cfg: EngineConfig
) -> tuple[float, dict[str, Any], float]:
    """Blend guide and comps. Returns (base_value, components, sigma_base)."""
    guide_mid = None
    guide_half = 0.0
    if m.guide_trade_low is not None and m.guide_trade_high is not None:
        guide_mid = (m.guide_trade_low + m.guide_trade_high) / 2
        guide_half = abs(m.guide_trade_high - m.guide_trade_low) / 2

    weights: list[float] = []
    prices: list[float] = []
    for c in m.comps:
        age_days = max(0, (m.valuation_date - c.sold_at).days)
        w = (0.5 ** (age_days / cfg.comps_half_life_days)) * max(0.0, min(1.0, c.match_quality))
        # Normalise the comp to the subject's odometer: a comp with fewer km would sell for more.
        km_diff_10k = (c.odometer_km - v.odometer_km) / 10_000
        price_adj = c.price_aud * (1 + cfg.km_adjust_per_10k * km_diff_10k)
        weights.append(w)
        prices.append(price_adj)

    sum_w = sum(weights)
    comps_weighted = sum(w * p for w, p in zip(weights, prices, strict=True)) / sum_w if sum_w > 0 else None
    comps_sigma = 0.0
    if comps_weighted is not None and sum_w > 0:
        var = sum(w * (p - comps_weighted) ** 2 for w, p in zip(weights, prices, strict=True)) / sum_w
        comps_sigma = math.sqrt(var)

    if guide_mid is None and comps_weighted is None:
        raise InsufficientMarketData("no guide range and no comparables")

    if guide_mid is None:
        cw = 1.0
        base = comps_weighted
    elif comps_weighted is None:
        cw = 0.0
        base = guide_mid
    else:
        cw = min(cfg.comps_max_weight, 0.3 * sum_w)
        base = (1 - cw) * guide_mid + cw * comps_weighted

    sigma = math.sqrt(((1 - cw) * guide_half) ** 2 + (cw * comps_sigma) ** 2)
    if guide_mid is None:
        sigma = max(sigma, 0.06 * base)  # comps-only is inherently noisier
    if comps_weighted is None:
        sigma = max(sigma, 0.05 * base)

    components = {
        "guide_mid": _round_money(guide_mid) if guide_mid is not None else None,
        "guide_half_width": _round_money(guide_half),
        "comps_weighted": _round_money(comps_weighted) if comps_weighted is not None else None,
        "comps_effective_n": round(sum_w, 3),
        "comps_n": len(m.comps),
        "comps_sigma": _round_money(comps_sigma),
        "comps_weight": round(cw, 3),
        "sigma_base": _round_money(sigma),
    }
    return _round_money(base), components, sigma


def condition_adjustments(v: VehicleInputs, base: float) -> list[Line]:
    lines: list[Line] = []
    vf = v.verified_fields
    if v.service_history in SERVICE_HISTORY_ADJ and SERVICE_HISTORY_ADJ[v.service_history]:
        lines.append(
            Line(
                "service_history",
                f"Service history: {v.service_history}",
                _round_money(base * SERVICE_HISTORY_ADJ[v.service_history]),
                "service_history" in vf,
            )
        )
    if v.prior_damage:
        lines.append(
            Line(
                "prior_damage",
                "Prior damage / repairs",
                _round_money(base * PRIOR_DAMAGE_ADJ),
                "prior_damage" in vf,
            )
        )
    if v.write_off_status and v.write_off_status.get("written_off"):
        lines.append(
            Line(
                "write_off",
                f"Write-off history ({v.write_off_status.get('type') or 'unspecified'})",
                _round_money(base * WRITE_OFF_ADJ),
                "write_off_status" in vf,
                note="usually a hard pass — escalate",
            )
        )
    if v.panel_paint_condition == "poor":
        lines.append(
            Line(
                "panel_stigma",
                "Poor presentation beyond repair cost",
                _round_money(base * PANEL_POOR_STIGMA_ADJ),
                "panel_paint_condition" in vf,
            )
        )
    if v.mechanical_faults and v.mechanical_faults.get("warning_lights"):
        lines.append(
            Line(
                "warning_lights",
                "Warning lights: undiagnosed-fault uncertainty",
                _round_money(base * WARNING_LIGHT_UNCERTAINTY_ADJ),
                "mechanical_faults" in vf,
            )
        )
    status = (v.rego_status or {}).get("status")
    if status == "expired":
        lines.append(
            Line(
                "rego_expired",
                "Registration expired",
                _round_money(base * REGO_EXPIRED_ADJ),
                "rego_status" in vf,
            )
        )
    elif status == "unregistered":
        lines.append(
            Line(
                "rego_unregistered",
                "Unregistered",
                _round_money(base * REGO_UNREGISTERED_ADJ),
                "rego_status" in vf,
            )
        )
    return lines


def market_adjustments(v: VehicleInputs, m: MarketInputs, cfg: EngineConfig, base: float) -> list[Line]:
    lines: list[Line] = []
    if m.days_supply is not None:
        if m.days_supply > 90:
            pct = -0.06
        elif m.days_supply > 60:
            pct = -0.03
        elif m.days_supply < 30:
            pct = 0.02
        else:
            pct = 0.0
        if pct:
            lines.append(
                Line("days_supply", f"Days' supply {m.days_supply:.0f}", _round_money(base * pct), True)
            )
    season = cfg.seasonality.get(v.segment, {}).get(m.valuation_date.month, 1.0)
    if season != 1.0:
        lines.append(
            Line(
                "seasonality",
                f"Seasonality ({v.segment}, month {m.valuation_date.month})",
                _round_money(base * (season - 1)),
                True,
            )
        )
    pos = {"long": -0.03, "short": 0.02}.get(m.stock_position or "", 0.0)
    if pos:
        lines.append(
            Line("stock_position", f"Stock position: {m.stock_position}", _round_money(base * pos), True)
        )
    return lines


def recon_line_items(v: VehicleInputs) -> list[Line]:
    seg = v.segment if v.segment in TYRE_EACH_BY_SEGMENT else "medium"
    vf = v.verified_fields
    lines: list[Line] = [
        Line("detail", "Detail and presentation", DETAIL_AUD, True),
        Line("safety_check", "Safety check", SAFETY_CHECK_AUD, True),
    ]
    for fld, expected in EXPECTED_RECON_WHEN_UNKNOWN.items():
        if getattr(v, fld) is None:
            lines.append(
                Line(f"expected_{fld}", f"{fld.replace('_', ' ')} unknown — expected spend", expected, False)
            )
    if v.service_history in {"partial", "none"}:
        lines.append(
            Line("service", "Full service (history incomplete)", FULL_SERVICE_AUD, "service_history" in vf)
        )
    pp = v.panel_paint_condition
    if pp in PANEL_PAINT_RECON and PANEL_PAINT_RECON[pp]:
        lines.append(
            Line(
                "panel_paint", f"Panel and paint: {pp}", PANEL_PAINT_RECON[pp], "panel_paint_condition" in vf
            )
        )
    mf = v.mechanical_faults or {}
    if mf and not mf.get("none"):
        items = mf.get("items") or []
        if mf.get("warning_lights"):
            lines.append(
                Line(
                    "warning_light_diag",
                    "Warning light diagnosis",
                    WARNING_LIGHT_DIAG_AUD,
                    "mechanical_faults" in vf,
                )
            )
        for i, item in enumerate(items[:6]):
            lines.append(
                Line(
                    f"fault_{i + 1}",
                    f"Fault: {str(item)[:60]}",
                    MECHANICAL_FAULT_EACH_AUD,
                    "mechanical_faults" in vf,
                )
            )
    tyre_each = TYRE_EACH_BY_SEGMENT[seg]
    if v.tyre_condition == "worn":
        lines.append(Line("tyres", "Tyres: replace two", 2 * tyre_each, "tyre_condition" in vf))
    elif v.tyre_condition == "replace":
        lines.append(Line("tyres", "Tyres: replace four", 4 * tyre_each, "tyre_condition" in vf))
    if v.keys_count is not None and v.keys_count < 2:
        lines.append(Line("keys", "Second key", KEY_REPLACEMENT_BY_SEGMENT[seg], "keys_count" in vf))
    status = (v.rego_status or {}).get("status")
    if status == "expired":
        lines.append(Line("rwc", "Roadworthy certificate", RWC_AUD, "rego_status" in vf))
    elif status == "unregistered":
        lines.append(Line("rwc", "Roadworthy certificate", RWC_AUD, "rego_status" in vf))
        lines.append(Line("rego", "Registration", REGO_RENEWAL_AUD, "rego_status" in vf))
    return lines


def verified_share(v: VehicleInputs) -> float:
    """Proportion of the condition picture that has been verified visually rather than asserted."""
    return sum(1 for f in CONDITION_FIELDS if f in v.verified_fields) / len(CONDITION_FIELDS)


def contingency_rate(share: float, cfg: EngineConfig) -> float:
    return cfg.contingency_floor + (cfg.contingency_unverified - cfg.contingency_floor) * (1 - share)


def build_ladder(wholesale_max: float, cfg: EngineConfig) -> dict[str, float]:
    step = cfg.ladder_rounding_aud
    return {
        "opening": _floor_to(wholesale_max * cfg.ladder_opening, step),
        "step_1": _floor_to(wholesale_max * cfg.ladder_step_1, step),
        "step_2": _floor_to(wholesale_max * cfg.ladder_step_2, step),
        "floor": _floor_to(wholesale_max, step),
    }


def compute(v: VehicleInputs, m: MarketInputs, cfg: EngineConfig | None = None) -> ValuationResult:
    cfg = cfg or EngineConfig()
    warnings: list[str] = []

    base, components, sigma_base = compute_base_value(v, m, cfg)

    cond_lines = condition_adjustments(v, base)
    condition_adj = _round_money(sum(line.amount for line in cond_lines))

    mkt_lines = market_adjustments(v, m, cfg, base)
    market_adj = _round_money(sum(line.amount for line in mkt_lines))
    if m.days_supply is None:
        warnings.append("days_supply unknown — no market adjustment applied")

    recon_lines = recon_line_items(v)
    recon_subtotal = _round_money(sum(line.amount for line in recon_lines))
    share = verified_share(v)
    rate = contingency_rate(share, cfg)
    contingency = _round_money(recon_subtotal * rate)
    recon_estimate = _round_money(recon_subtotal + contingency)

    for f in CONDITION_FIELDS:
        if getattr(v, f) is None:
            warnings.append(f"{f} unknown — expected-spend line applied; contingency covers the gap")

    adjusted = base + condition_adj + market_adj
    margin = _round_money(adjusted * cfg.margin_for(v.segment))
    wholesale_max = _round_money(adjusted - recon_estimate - margin - cfg.transport_cost_aud)
    if wholesale_max <= 0:
        warnings.append("wholesale_max is not positive — vehicle is not viable at target margin")
        wholesale_max = 0.0

    sigma_recon = math.sqrt((0.10 * recon_subtotal) ** 2 + (0.5 * recon_subtotal * (1 - share)) ** 2)
    half_width = _round_money(math.sqrt(sigma_base**2 + sigma_recon**2))
    band_low = _round_money(max(0.0, wholesale_max - half_width))
    band_high = _round_money(wholesale_max + half_width)

    fo = v.finance_owing or {}
    if fo.get("owing") and fo.get("amount_aud") and float(fo["amount_aud"]) > wholesale_max:
        warnings.append("encumbered_above_offer: finance owing exceeds wholesale_max — escalate")
    if v.write_off_status and v.write_off_status.get("written_off"):
        warnings.append("write_off: valuation is indicative only — escalate")

    snapshot = {
        "vehicle": {**asdict(v), "verified_fields": sorted(v.verified_fields)},
        "market": {
            **{k: val for k, val in asdict(m).items() if k != "comps"},
            "comps": [asdict(c) for c in m.comps],
        },
        "config": asdict(cfg),
    }
    snapshot = _jsonable(snapshot)

    return ValuationResult(
        engine_version=ENGINE_VERSION,
        base_value=base,
        base_components=components,
        condition_adj=condition_adj,
        condition_lines=cond_lines,
        market_adj=market_adj,
        market_lines=mkt_lines,
        recon_lines=recon_lines,
        recon_subtotal=recon_subtotal,
        contingency=contingency,
        contingency_rate=round(rate, 4),
        verified_share=round(share, 3),
        recon_estimate=recon_estimate,
        target_margin=margin,
        transport_cost=_round_money(cfg.transport_cost_aud),
        wholesale_max=wholesale_max,
        band_low=band_low,
        band_high=band_high,
        ladder=build_ladder(wholesale_max, cfg),
        warnings=warnings,
        inputs_snapshot=snapshot,
    )


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj
