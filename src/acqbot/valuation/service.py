"""Valuation service — reads the fact store and market data, runs the engine, persists the result.

Basis rules (Section 6.2):
    verified    identity (year, odometer) and the PPSR/rego results all come from verified sources.
                Condition claims may still be verbal — the contingency prices that uncertainty and
                every offer is subject to inspection. This is the only basis that can be released.
    indicative  anything less. Internal triage only; never shown to the seller or the model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acqbot.config import get_settings
from acqbot.conversation.transitions import escalate
from acqbot.enrichment.catalog import segment_for
from acqbot.facts.store import FactSheet, fact_sheet
from acqbot.models import Lead, MarketData, MarketDataKind, Valuation, ValuationBasis
from acqbot.queue.worker import job_handler
from acqbot.valuation import engine
from acqbot.valuation.engine import CompInput, EngineConfig, MarketInputs, VehicleInputs

RELEASE_REQUIRES_VERIFIED = (
    "year",
    "odometer_km",
    "finance_owing",
    "write_off_status",
    "stolen",
    "rego_status",
)


@dataclass
class ValuationOutcome:
    row: Valuation
    result: engine.ValuationResult
    basis: ValuationBasis
    escalated: str | None = None


def engine_config_from_settings() -> EngineConfig:
    s = get_settings()
    return EngineConfig(
        target_margin_pct=s.target_margin_pct,
        target_margin_by_segment=dict(s.target_margin_by_segment),
        transport_cost_aud=s.transport_cost_aud,
        ladder_opening=s.ladder_opening,
        ladder_step_1=s.ladder_step_1,
        ladder_step_2=s.ladder_step_2,
    )


def _latest_market(session: Session, lead_id: uuid.UUID, kind: MarketDataKind) -> MarketData | None:
    stmt = (
        select(MarketData)
        .where(MarketData.lead_id == lead_id, MarketData.kind == kind)
        .order_by(MarketData.fetched_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def vehicle_inputs_from_sheet(sheet: FactSheet) -> VehicleInputs:
    make, model = sheet.get("make"), sheet.get("model")
    if not make or not model or sheet.get("year") is None or sheet.get("odometer_km") is None:
        raise ValueError("fact sheet lacks make/model/year/odometer")
    mf = sheet.get("mechanical_faults")
    if isinstance(mf, str):
        mf = (
            {"none": True}
            if mf.strip().lower() in {"none", "no", "nil"}
            else {"items": [mf], "warning_lights": False}
        )
    return VehicleInputs(
        make=make,
        model=model,
        variant=sheet.get("variant"),
        year=int(sheet.get("year")),
        odometer_km=int(sheet.get("odometer_km")),
        segment=segment_for(make, model),
        service_history=sheet.get("service_history"),
        panel_paint_condition=sheet.get("panel_paint_condition"),
        mechanical_faults=mf,
        tyre_condition=sheet.get("tyre_condition"),
        keys_count=int(sheet.get("keys_count")) if sheet.get("keys_count") is not None else None,
        rego_status=sheet.get("rego_status"),
        finance_owing=sheet.get("finance_owing"),
        write_off_status=sheet.get("write_off_status"),
        prior_damage=sheet.get("prior_damage"),
        verified_fields={k for k, fv in sheet.facts.items() if fv.verified},
    )


def market_inputs_from_rows(guide: MarketData | None, comps: MarketData | None) -> MarketInputs:
    m = MarketInputs()
    if guide is not None:
        p = guide.payload
        m.guide_trade_low = float(p["trade_low"])
        m.guide_trade_high = float(p["trade_high"])
        m.guide_as_of = _as_date(p.get("as_of")) if p.get("as_of") else None
    if comps is not None:
        for c in comps.payload.get("comps", []):
            m.comps.append(
                CompInput(
                    sold_at=_as_date(c["sold_at"]),
                    price_aud=float(c["price_aud"]),
                    odometer_km=int(c["odometer_km"]),
                    match_quality=float(c.get("match_quality", 1.0)),
                    source=str(c.get("source", "")),
                )
            )
    return m


def missing_for_release(sheet: FactSheet) -> list[str]:
    """Which release-gating facts are not yet verified (empty means the valuation can be released)."""
    missing = [k for k in RELEASE_REQUIRES_VERIFIED if not (sheet.facts.get(k) and sheet.facts[k].verified)]
    if sheet.get("stolen") is True:
        missing.append("stolen")
    if (sheet.get("write_off_status") or {}).get("written_off"):
        missing.append("write_off_status")
    return missing


def determine_basis(sheet: FactSheet) -> ValuationBasis:
    for key in RELEASE_REQUIRES_VERIFIED:
        fv = sheet.facts.get(key)
        if fv is None or not fv.verified:
            return ValuationBasis.INDICATIVE
    if sheet.get("stolen") is True or (sheet.get("write_off_status") or {}).get("written_off"):
        return ValuationBasis.INDICATIVE
    return ValuationBasis.VERIFIED


def compute_valuation(session: Session, lead_id: uuid.UUID, *, persist: bool = True) -> ValuationOutcome:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise ValueError(f"lead {lead_id} not found")
    sheet = fact_sheet(session, lead_id)
    v = vehicle_inputs_from_sheet(sheet)
    m = market_inputs_from_rows(
        _latest_market(session, lead_id, MarketDataKind.GUIDE),
        _latest_market(session, lead_id, MarketDataKind.COMPS),
    )
    result = engine.compute(v, m, engine_config_from_settings())
    basis = determine_basis(sheet)

    row = Valuation(
        lead_id=lead_id,
        basis=basis,
        engine_version=result.engine_version,
        band_low=result.band_low,
        band_high=result.band_high,
        wholesale_max=result.wholesale_max,
        ladder=result.ladder,
        recon_estimate=result.recon_estimate,
        recon_lines=[line.__dict__ for line in result.recon_lines],
        inputs_snapshot={
            **result.inputs_snapshot,
            "fact_ids": {k: str(fv.fact_id) for k, fv in sheet.facts.items()},
            "contradicted": sheet.contradicted,
            "base_components": result.base_components,
            "condition_lines": [line.__dict__ for line in result.condition_lines],
            "market_lines": [line.__dict__ for line in result.market_lines],
            "contingency": result.contingency,
            "contingency_rate": result.contingency_rate,
            "verified_share": result.verified_share,
            "target_margin": result.target_margin,
            "transport_cost": result.transport_cost,
            "warnings": result.warnings,
        },
    )
    escalated = None
    if persist:
        session.add(row)
        session.flush()
        if any(w.startswith("encumbered_above_offer") for w in result.warnings):
            escalate(
                session,
                lead,
                "encumbered_above_offer",
                {"finance_owing": v.finance_owing, "wholesale_max": result.wholesale_max},
            )
            escalated = "encumbered_above_offer"
    return ValuationOutcome(row=row, result=result, basis=basis, escalated=escalated)


def latest_valuation(
    session: Session, lead_id: uuid.UUID, *, basis: ValuationBasis | None = None
) -> Valuation | None:
    stmt = select(Valuation).where(Valuation.lead_id == lead_id)
    if basis is not None:
        stmt = stmt.where(Valuation.basis == basis)
    stmt = stmt.order_by(Valuation.computed_at.desc(), Valuation.valuation_id).limit(1)
    return session.scalars(stmt).first()


@job_handler("value_lead")
def _handle_value_lead(session: Session, payload: dict[str, Any]) -> None:
    from acqbot.conversation.transitions import create_task
    from acqbot.queue.jobs import enqueue

    lead_id = uuid.UUID(payload["lead_id"])
    out = compute_valuation(session, lead_id)
    if payload.get("then") != "on_priced" or out.escalated is not None:
        return
    if out.basis == ValuationBasis.VERIFIED:
        enqueue(session, "on_priced", {"lead_id": str(lead_id)}, dedupe_key=f"on_priced:{lead_id}")
        return
    # Indicative only: a person must verify what the automation cannot (Phase 3: the dash-photo odometer).
    lead = session.get(Lead, lead_id)
    sheet = fact_sheet(session, lead_id)
    create_task(
        session,
        lead,
        "verification_review",
        {
            "missing_verified": missing_for_release(sheet),
            "claimed": {k: sheet.get(k) for k in missing_for_release(sheet)},
            "photos": sheet.get("photos"),
            "indicative_wholesale_max": out.result.wholesale_max,
        },
    )
