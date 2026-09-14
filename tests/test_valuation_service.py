from pathlib import Path

from acqbot.enrichment.pipeline import enrich_lead
from acqbot.facts.store import fact_sheet, record_fact
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Escalation, FactSource, Lead, LeadState, Valuation, ValuationBasis
from acqbot.simulator import make_lead
from acqbot.valuation import calibration
from acqbot.valuation.service import compute_valuation, determine_basis, latest_valuation


def _enriched_lead(session, scenario="clean", seed=70):
    payload = make_lead(scenario, seed=seed)
    payload["vehicle_claimed"]["vin"] = payload["vehicle_claimed"]["vin"] or None
    lead_id = ingest_lead(session, payload).lead_id
    session.commit()
    enrich_lead(session, lead_id)
    session.commit()
    return lead_id


def test_fresh_lead_valuation_is_indicative_and_persisted(session):
    lead_id = _enriched_lead(session)
    out = compute_valuation(session, lead_id)
    session.commit()
    assert out.basis == ValuationBasis.INDICATIVE  # odometer is still a listing claim
    row = latest_valuation(session, lead_id)
    assert row is not None and row.basis == ValuationBasis.INDICATIVE
    assert float(row.band_low) <= float(row.wholesale_max) <= float(row.band_high)
    assert set(row.ladder) == {"opening", "step_1", "step_2", "floor"}
    assert row.inputs_snapshot["fact_ids"]["make"]
    assert row.engine_version == "2.0.0"
    assert latest_valuation(session, lead_id, basis=ValuationBasis.VERIFIED) is None


def test_basis_becomes_verified_once_identity_and_checks_are_verified(session):
    lead_id = _enriched_lead(session, seed=71)
    sheet = fact_sheet(session, lead_id)
    assert determine_basis(sheet) == ValuationBasis.INDICATIVE
    # Dash photo confirms the odometer (year already verified by VIN decode; PPSR + rego by enrichment).
    record_fact(
        session, lead_id, "odometer_km", sheet.get("odometer_km"), source=FactSource.PHOTO, verified=True
    )
    session.commit()
    assert determine_basis(fact_sheet(session, lead_id)) == ValuationBasis.VERIFIED
    out = compute_valuation(session, lead_id)
    session.commit()
    assert out.basis == ValuationBasis.VERIFIED
    assert latest_valuation(session, lead_id, basis=ValuationBasis.VERIFIED) is not None


def test_conversation_facts_change_the_price(session):
    lead_id = _enriched_lead(session, seed=72)
    before = compute_valuation(session, lead_id, persist=False).result
    for key, val in [
        ("service_history", "none"),
        ("tyre_condition", "replace"),
        ("keys_count", 1),
        ("panel_paint_condition", "poor"),
        ("mechanical_faults", {"items": ["aircon not cold"], "warning_lights": True}),
    ]:
        record_fact(session, lead_id, key, val, source=FactSource.SELLER, confidence=0.9)
    session.commit()
    after = compute_valuation(session, lead_id, persist=False).result
    assert after.wholesale_max < before.wholesale_max
    codes = {line.code for line in after.recon_lines}
    assert {"tyres", "keys", "panel_paint", "warning_light_diag", "fault_1", "service"} <= codes
    assert not any(c.startswith("expected_") for c in codes)


def test_encumbrance_above_offer_escalates(session):
    lead_id = _enriched_lead(session, "encumbered", seed=73)
    record_fact(
        session,
        lead_id,
        "finance_owing",
        {
            "owing": True,
            "amount_aud": 999_999,
            "secured_parties": ["Bank"],
            "checked_at": "2026-09-14T00:00:00+00:00",
        },
        source=FactSource.PPSR,
        verified=True,
    )
    session.commit()
    out = compute_valuation(session, lead_id)
    session.commit()
    assert out.escalated == "encumbered_above_offer"
    assert session.get(Lead, lead_id).state == LeadState.HUMAN
    assert session.query(Escalation).filter_by(lead_id=lead_id, reason="encumbered_above_offer").count() == 1


def test_valuations_are_append_only_and_latest_wins(session):
    lead_id = _enriched_lead(session, seed=74)
    compute_valuation(session, lead_id)
    session.commit()
    record_fact(session, lead_id, "tyre_condition", "replace", source=FactSource.SELLER, confidence=0.9)
    second = compute_valuation(session, lead_id)
    session.commit()
    assert session.query(Valuation).filter_by(lead_id=lead_id).count() == 2
    assert latest_valuation(session, lead_id).valuation_id == second.row.valuation_id


def test_calibration_harness_runs_on_synthetic_fixture(tmp_path: Path):
    rows = calibration.synthetic_rows(60, seed=3)
    csv_path = tmp_path / "fixture.csv"
    calibration.write_csv(rows, csv_path)
    loaded = calibration.load_csv(csv_path)
    assert len(loaded) == 60 and set(loaded[0]) == set(calibration.COLUMNS)
    report = calibration.run(loaded, synthetic=True)
    assert report.n == 60 and report.skipped == []
    assert report.recon_mae > 0 and 0 < report.recon_coverage < 3
    assert "NOT a calibration" in report.render()
    assert set(report.by_segment) <= {"small", "medium", "large", "suv", "ute", "prestige"}


def test_calibration_skips_bad_rows_and_reports_them():
    rows = calibration.synthetic_rows(5, seed=4)
    rows[2]["guide_trade_low"] = rows[2]["guide_trade_high"] = ""
    rows[2]["comps_json"] = "[]"
    rows[4]["year"] = "not-a-year"
    report = calibration.run(rows)
    assert report.n == 3 and [s["row"] for s in report.skipped] == [3, 5]
