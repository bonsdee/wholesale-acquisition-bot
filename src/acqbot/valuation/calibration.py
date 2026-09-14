"""Calibration harness — Section 12, phase 2: "run against at least two hundred historical
transactions with known outcomes. Tune until the error distribution is acceptable and the recon
model is calibrated against actual spend. Do not proceed until this passes."

Input: a CSV of historical transactions, one row per vehicle bought. Columns:

    make, model, variant, year, odometer_km, segment (optional)
    service_history            full | partial | none
    panel_paint_condition      excellent | good | fair | poor
    mechanical_faults          none | <free text, ';' separated> ; prefix "WL:" if warning lights were on
    tyre_condition             new | good | worn | replace
    keys_count                 integer
    rego_status                current | expired | unregistered
    guide_trade_low, guide_trade_high     the guide range at the time
    comps_json                 optional JSON list of {sold_at, price_aud, odometer_km, match_quality}
    purchase_price_aud         what was actually paid
    actual_recon_aud           what reconditioning actually cost
    resale_price_aud           optional — what it sold for
    purchased_at               YYYY-MM-DD

The report answers three questions: is the recon model honest (predicted vs actual spend)? does
`wholesale_max` sit where the business actually needed it (vs purchase price and realised margin)?
and where do the errors cluster (by segment)? A synthetic fixture exercises the harness until real
data exists; its numbers are not a calibration and the report says so.
"""

from __future__ import annotations

import csv
import io
import json
import random
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from acqbot.enrichment import catalog
from acqbot.enrichment.stubs import stub_trade_mid
from acqbot.valuation import engine
from acqbot.valuation.engine import CompInput, EngineConfig, MarketInputs, VehicleInputs

COLUMNS = [
    "make",
    "model",
    "variant",
    "year",
    "odometer_km",
    "segment",
    "service_history",
    "panel_paint_condition",
    "mechanical_faults",
    "tyre_condition",
    "keys_count",
    "rego_status",
    "guide_trade_low",
    "guide_trade_high",
    "comps_json",
    "purchase_price_aud",
    "actual_recon_aud",
    "resale_price_aud",
    "purchased_at",
]


@dataclass
class RowResult:
    row: int
    segment: str
    predicted_wholesale_max: float
    predicted_recon: float
    purchase_price: float
    actual_recon: float
    resale_price: float | None
    warnings: list[str] = field(default_factory=list)

    @property
    def recon_error(self) -> float:
        return self.predicted_recon - self.actual_recon

    @property
    def price_gap(self) -> float:
        """wholesale_max − what was paid. Negative means we'd have offered less than the deal needed."""
        return self.predicted_wholesale_max - self.purchase_price

    @property
    def realised_margin_pct(self) -> float | None:
        if self.resale_price is None or self.resale_price <= 0:
            return None
        return (self.resale_price - self.purchase_price - self.actual_recon) / self.resale_price


@dataclass
class Report:
    n: int
    synthetic: bool
    recon_mae: float
    recon_bias: float
    recon_mape: float
    recon_p90_abs: float
    recon_coverage: float  # sum predicted / sum actual
    price_gap_mean: float
    price_gap_p10: float
    price_gap_p90: float
    share_offer_below_paid: float
    realised_margin_median: float | None
    by_segment: dict[str, dict[str, float]]
    skipped: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__

    def render(self) -> str:
        head = (
            "SYNTHETIC FIXTURE — exercises the harness only; NOT a calibration"
            if self.synthetic
            else "Calibration report"
        )
        lines = [
            head,
            f"transactions: {self.n}   skipped: {len(self.skipped)}",
            "",
            "Reconditioning model (predicted − actual spend)",
            f"  MAE ${self.recon_mae:,.0f}   bias ${self.recon_bias:+,.0f}   MAPE {self.recon_mape:.1%}   p90 |err| ${self.recon_p90_abs:,.0f}",
            f"  coverage (Σ predicted / Σ actual): {self.recon_coverage:.2f}   (>1 over-estimates recon, <1 leaks margin)",
            "",
            "wholesale_max vs price actually paid",
            f"  mean gap ${self.price_gap_mean:+,.0f}   p10 ${self.price_gap_p10:+,.0f}   p90 ${self.price_gap_p90:+,.0f}",
            f"  share of deals where the ladder ceiling was below what was paid: {self.share_offer_below_paid:.0%}",
        ]
        if self.realised_margin_median is not None:
            lines.append(f"  realised margin (median, where resale known): {self.realised_margin_median:.1%}")
        lines += ["", "By segment (n, recon MAE, recon bias, mean price gap)"]
        for seg, s in sorted(self.by_segment.items()):
            lines.append(
                f"  {seg:9s} n={int(s['n']):3d}  MAE ${s['recon_mae']:,.0f}  bias ${s['recon_bias']:+,.0f}  gap ${s['price_gap_mean']:+,.0f}"
            )
        return "\n".join(lines)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _parse_faults(text: str) -> dict[str, Any] | None:
    t = (text or "").strip()
    if not t:
        return None
    if t.lower() in {"none", "nil", "no"}:
        return {"none": True}
    wl = t.upper().startswith("WL:")
    items = (
        [x.strip() for x in t[3:].split(";") if x.strip()]
        if wl
        else [x.strip() for x in t.split(";") if x.strip()]
    )
    return {"items": items, "warning_lights": wl}


def row_to_inputs(r: dict[str, str]) -> tuple[VehicleInputs, MarketInputs]:
    make, model = r["make"], r["model"]
    seg = r.get("segment") or catalog.segment_for(make, model)
    v = VehicleInputs(
        make=make,
        model=model,
        variant=r.get("variant") or None,
        year=int(r["year"]),
        odometer_km=int(float(r["odometer_km"])),
        segment=seg,
        service_history=r.get("service_history") or None,
        panel_paint_condition=r.get("panel_paint_condition") or None,
        mechanical_faults=_parse_faults(r.get("mechanical_faults", "")),
        tyre_condition=r.get("tyre_condition") or None,
        keys_count=int(r["keys_count"]) if r.get("keys_count") else None,
        rego_status={"status": r["rego_status"]} if r.get("rego_status") else None,
        # Historical transactions were inspected before purchase: everything is verified.
        verified_fields=set(engine.CONDITION_FIELDS) | {"year", "odometer_km", "rego_status"},
    )
    purchased = date.fromisoformat(r["purchased_at"]) if r.get("purchased_at") else date.today()
    m = MarketInputs(
        guide_trade_low=float(r["guide_trade_low"]) if r.get("guide_trade_low") else None,
        guide_trade_high=float(r["guide_trade_high"]) if r.get("guide_trade_high") else None,
        valuation_date=purchased,
    )
    if r.get("comps_json"):
        for c in json.loads(r["comps_json"]):
            m.comps.append(
                CompInput(
                    sold_at=date.fromisoformat(c["sold_at"]),
                    price_aud=float(c["price_aud"]),
                    odometer_km=int(c["odometer_km"]),
                    match_quality=float(c.get("match_quality", 1.0)),
                )
            )
    return v, m


def run(rows: list[dict[str, str]], cfg: EngineConfig | None = None, *, synthetic: bool = False) -> Report:
    cfg = cfg or EngineConfig()
    results: list[RowResult] = []
    skipped: list[dict[str, Any]] = []
    for i, r in enumerate(rows, start=1):
        try:
            v, m = row_to_inputs(r)
            res = engine.compute(v, m, cfg)
            results.append(
                RowResult(
                    row=i,
                    segment=v.segment,
                    predicted_wholesale_max=res.wholesale_max,
                    predicted_recon=res.recon_estimate,
                    purchase_price=float(r["purchase_price_aud"]),
                    actual_recon=float(r["actual_recon_aud"]),
                    resale_price=float(r["resale_price_aud"]) if r.get("resale_price_aud") else None,
                    warnings=res.warnings,
                )
            )
        except (KeyError, ValueError, engine.InsufficientMarketData) as exc:
            skipped.append({"row": i, "error": f"{type(exc).__name__}: {exc}"})

    if not results:
        raise ValueError("no usable rows")

    recon_err = [x.recon_error for x in results]
    abs_err = [abs(e) for e in recon_err]
    gaps = [x.price_gap for x in results]
    margins = [m for m in (x.realised_margin_pct for x in results) if m is not None]
    by_seg: dict[str, dict[str, float]] = {}
    for seg in {x.segment for x in results}:
        xs = [x for x in results if x.segment == seg]
        by_seg[seg] = {
            "n": len(xs),
            "recon_mae": statistics.mean(abs(x.recon_error) for x in xs),
            "recon_bias": statistics.mean(x.recon_error for x in xs),
            "price_gap_mean": statistics.mean(x.price_gap for x in xs),
        }
    return Report(
        n=len(results),
        synthetic=synthetic,
        recon_mae=statistics.mean(abs_err),
        recon_bias=statistics.mean(recon_err),
        recon_mape=statistics.mean(
            abs(x.recon_error) / x.actual_recon for x in results if x.actual_recon > 0
        ),
        recon_p90_abs=_pct(abs_err, 0.9),
        recon_coverage=sum(x.predicted_recon for x in results)
        / max(1.0, sum(x.actual_recon for x in results)),
        price_gap_mean=statistics.mean(gaps),
        price_gap_p10=_pct(gaps, 0.1),
        price_gap_p90=_pct(gaps, 0.9),
        share_offer_below_paid=sum(1 for g in gaps if g < 0) / len(gaps),
        realised_margin_median=statistics.median(margins) if margins else None,
        by_segment=by_seg,
        skipped=skipped,
    )


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def synthetic_rows(n: int = 200, seed: int = 1) -> list[dict[str, str]]:
    """Plausible historical transactions with noisy recon spend and prices. For harness testing only."""
    rng = random.Random(seed)
    rows: list[dict[str, str]] = []
    today = date.today()
    for _ in range(n):
        e = rng.choice(catalog.CATALOG)
        year = rng.randint(today.year - 11, today.year - 1)
        age = max(1, today.year - year)
        km = int(max(5_000, rng.gauss(age * 15_000, age * 4_500)))
        mid = stub_trade_mid(e.make, e.model, year, km)
        purchased = today - timedelta(days=rng.randint(30, 540))
        panel = rng.choices(["excellent", "good", "fair", "poor"], [15, 50, 27, 8])[0]
        tyres = rng.choices(["new", "good", "worn", "replace"], [10, 55, 25, 10])[0]
        faults = rng.choices(
            ["none", "WL:check engine light", "aircon weak", "WL:abs; rear brakes"], [72, 10, 12, 6]
        )[0]
        v = VehicleInputs(
            make=e.make,
            model=e.model,
            variant=rng.choice(e.variants),
            year=year,
            odometer_km=km,
            segment=e.segment,
            service_history=rng.choices(["full", "partial", "none"], [50, 35, 15])[0],
            panel_paint_condition=panel,
            mechanical_faults=_parse_faults(faults),
            tyre_condition=tyres,
            keys_count=rng.choices([1, 2], [30, 70])[0],
            rego_status={"status": rng.choices(["current", "expired", "unregistered"], [85, 10, 5])[0]},
            verified_fields=set(engine.CONDITION_FIELDS),
        )
        model_recon = engine.recon_line_items(v)
        base_recon = sum(line.amount for line in model_recon)
        # Real spend is noisy and has surprises the line items did not anticipate.
        actual_recon = max(
            150.0,
            base_recon * rng.lognormvariate(0.05, 0.35) + (rng.random() < 0.15) * rng.uniform(300, 1_800),
        )
        comps = [
            {
                "sold_at": (purchased - timedelta(days=rng.randint(5, 150))).isoformat(),
                "price_aud": round(mid * rng.uniform(0.9, 1.1), -1),
                "odometer_km": int(km * rng.uniform(0.75, 1.25)),
                "match_quality": round(rng.uniform(0.6, 1.0), 2),
            }
            for _ in range(rng.randint(3, 6))
        ]
        purchase = round(mid * rng.uniform(0.74, 0.92) - actual_recon * rng.uniform(0.6, 1.1), -1)
        resale = round(mid * rng.uniform(1.05, 1.22), -1) if rng.random() < 0.8 else ""
        rows.append(
            {
                "make": e.make,
                "model": e.model,
                "variant": v.variant or "",
                "year": str(year),
                "odometer_km": str(km),
                "segment": e.segment,
                "service_history": v.service_history,
                "panel_paint_condition": panel,
                "mechanical_faults": faults,
                "tyre_condition": tyres,
                "keys_count": str(v.keys_count),
                "rego_status": v.rego_status["status"],
                "guide_trade_low": str(round(mid * 0.94, -1)),
                "guide_trade_high": str(round(mid * 1.06, -1)),
                "comps_json": json.dumps(comps),
                "purchase_price_aud": str(max(500.0, purchase)),
                "actual_recon_aud": str(round(actual_recon, 0)),
                "resale_price_aud": str(resale),
                "purchased_at": purchased.isoformat(),
            }
        )
    return rows


def write_csv(rows: list[dict[str, str]], path: Path | None = None) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in COLUMNS})
    text = buf.getvalue()
    if path is not None:
        path.write_text(text, encoding="utf-8")
    return text
