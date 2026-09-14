from datetime import date, timedelta

import pytest

from acqbot.valuation import engine
from acqbot.valuation.engine import CompInput, EngineConfig, MarketInputs, VehicleInputs, compute

TODAY = date(2026, 9, 14)


def vehicle(**over) -> VehicleInputs:
    base = dict(
        make="Toyota",
        model="Corolla",
        variant="Ascent Sport",
        year=2019,
        odometer_km=84_000,
        segment="small",
        service_history="full",
        panel_paint_condition="good",
        mechanical_faults={"none": True},
        tyre_condition="good",
        keys_count=2,
        rego_status={"status": "current"},
        finance_owing={"owing": False},
        write_off_status={"written_off": False},
        verified_fields=set(engine.CONDITION_FIELDS) | {"year", "odometer_km", "rego_status"},
    )
    base.update(over)
    return VehicleInputs(**base)


def market(**over) -> MarketInputs:
    base = dict(
        guide_trade_low=19_000.0,
        guide_trade_high=21_000.0,
        comps=[
            CompInput(TODAY - timedelta(days=10), 20_500, 80_000, 0.95),
            CompInput(TODAY - timedelta(days=25), 19_800, 95_000, 0.9),
            CompInput(TODAY - timedelta(days=40), 21_000, 70_000, 0.85),
        ],
        valuation_date=TODAY,
    )
    base.update(over)
    return MarketInputs(**base)


def test_ladder_is_monotonic_rounded_and_capped_at_wholesale_max():
    r = compute(vehicle(), market())
    lad = r.ladder
    assert lad["opening"] < lad["step_1"] < lad["step_2"] < lad["floor"]
    assert all(v % 50 == 0 for v in lad.values())
    assert lad["floor"] <= r.wholesale_max < lad["floor"] + 50
    assert lad["opening"] <= r.wholesale_max * 0.88


def test_wholesale_max_identity_holds():
    r = compute(vehicle(), market(), EngineConfig(target_margin_pct=0.10, transport_cost_aud=250))
    adjusted = r.base_value + r.condition_adj + r.market_adj
    assert r.target_margin == pytest.approx(adjusted * 0.10, abs=0.01)
    assert r.wholesale_max == pytest.approx(adjusted - r.recon_estimate - r.target_margin - 250, abs=0.02)
    assert r.recon_estimate == pytest.approx(r.recon_subtotal + r.contingency, abs=0.01)


def test_contingency_scales_with_verification():
    full = compute(vehicle(), market())
    none = compute(vehicle(verified_fields={"year", "odometer_km"}), market())
    assert full.contingency_rate == pytest.approx(0.05)
    assert none.contingency_rate == pytest.approx(0.20)
    assert none.verified_share == 0.0 and full.verified_share == 1.0
    assert none.wholesale_max < full.wholesale_max
    # Less verification → wider band.
    assert (none.band_high - none.band_low) > (full.band_high - full.band_low)


def test_unknown_condition_uses_expected_spend_not_zero():
    r = compute(vehicle(panel_paint_condition=None, tyre_condition=None), market())
    codes = {line.code for line in r.recon_lines}
    assert "expected_panel_paint_condition" in codes and "expected_tyre_condition" in codes
    assert any("panel_paint_condition unknown" in w for w in r.warnings)
    known = compute(vehicle(), market())
    assert "expected_panel_paint_condition" not in {line.code for line in known.recon_lines}


def test_value_side_and_cost_side_do_not_double_count():
    r = compute(vehicle(tyre_condition="replace", service_history="none"), market())
    recon_codes = {line.code for line in r.recon_lines}
    cond_codes = {line.code for line in r.condition_lines}
    assert "tyres" in recon_codes and "tyres" not in cond_codes  # tyres are a cost, not a value deduction
    assert (
        "service_history" in cond_codes and "service" in recon_codes
    )  # missing history: both, different meanings
    tyres = next(line for line in r.recon_lines if line.code == "tyres")
    assert tyres.amount == 4 * engine.TYRE_EACH_BY_SEGMENT["small"]


def test_recent_matching_comps_dominate_old_ones():
    recent = market(comps=[CompInput(TODAY - timedelta(days=5), 24_000, 84_000, 1.0)])
    old = market(comps=[CompInput(TODAY - timedelta(days=400), 24_000, 84_000, 1.0)])
    r_recent, r_old = compute(vehicle(), recent), compute(vehicle(), old)
    assert r_recent.base_components["comps_weight"] > r_old.base_components["comps_weight"]
    assert r_recent.base_value > r_old.base_value
    assert r_old.base_value == pytest.approx(20_000, rel=0.01)  # essentially the guide midpoint


def test_comps_are_normalised_for_odometer():
    low_km = market(comps=[CompInput(TODAY - timedelta(days=5), 20_000, 40_000, 1.0)])
    high_km = market(comps=[CompInput(TODAY - timedelta(days=5), 20_000, 130_000, 1.0)])
    # A comp with fewer km than the subject implies the subject is worth less than that comp sold for.
    assert compute(vehicle(), low_km).base_components["comps_weighted"] < 20_000
    assert compute(vehicle(), high_km).base_components["comps_weighted"] > 20_000


def test_guide_only_and_comps_only_both_work_and_widen_uncertainty():
    both = compute(vehicle(), market())
    guide_only = compute(vehicle(), market(comps=[]))
    comps_only = compute(vehicle(), market(guide_trade_low=None, guide_trade_high=None))
    assert guide_only.base_value == pytest.approx(20_000)
    assert comps_only.base_components["comps_weight"] == 1.0
    assert guide_only.base_components["sigma_base"] >= both.base_components["sigma_base"]


def test_no_market_data_raises():
    with pytest.raises(engine.InsufficientMarketData):
        compute(vehicle(), MarketInputs(valuation_date=TODAY))


def test_write_off_and_encumbrance_raise_warnings():
    r = compute(
        vehicle(
            write_off_status={"written_off": True, "type": "repairable"},
            finance_owing={"owing": True, "amount_aud": 50_000},
        ),
        market(),
    )
    assert any(w.startswith("write_off") for w in r.warnings)
    assert any(w.startswith("encumbered_above_offer") for w in r.warnings)
    assert any(line.code == "write_off" for line in r.condition_lines)
    assert r.condition_adj < -0.2 * r.base_value


def test_market_adjustments():
    slow = compute(vehicle(), market(days_supply=120, stock_position="long"))
    fast = compute(vehicle(), market(days_supply=20, stock_position="short"))
    assert slow.market_adj < 0 < fast.market_adj
    assert slow.wholesale_max < fast.wholesale_max


def test_segment_margin_override():
    default = compute(vehicle(), market(), EngineConfig(target_margin_pct=0.10))
    ute_cfg = EngineConfig(target_margin_pct=0.10, target_margin_by_segment={"small": 0.05})
    cheaper = compute(vehicle(), market(), ute_cfg)
    assert cheaper.target_margin < default.target_margin
    assert cheaper.wholesale_max > default.wholesale_max


def test_non_viable_vehicle_floors_at_zero():
    r = compute(vehicle(), market(guide_trade_low=500, guide_trade_high=700, comps=[]))
    assert r.wholesale_max == 0.0 and any("not viable" in w for w in r.warnings)
    assert r.ladder["floor"] == 0


def test_snapshot_is_json_serialisable():
    import json

    r = compute(vehicle(), market())
    json.dumps(r.inputs_snapshot)
    assert r.inputs_snapshot["vehicle"]["make"] == "Toyota"
    assert len(r.inputs_snapshot["market"]["comps"]) == 3
