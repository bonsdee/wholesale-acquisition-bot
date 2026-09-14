from datetime import date

from acqbot.conversation.gate import GateContext, validate
from acqbot.models import LeadState

LADDER = {"opening": 16_150, "step_1": 17_100, "step_2": 17_800, "floor": 18_350}
TODAY = date(2026, 9, 14)


def ctx(stage, **kw):
    return GateContext(stage=stage, vehicle_year=2019, today=TODAY, **kw)


def test_no_figures_before_priced():
    r = validate("We could do around $17,000 for it", ctx(LeadState.DISCOVERY))
    assert not r.ok and any("before PRICED" in v for v in r.violations)
    assert validate("Odometer 84,500 km, two keys, 2019 model", ctx(LeadState.DISCOVERY)).ok


def test_figures_must_be_on_the_ladder():
    assert validate("Here's where we've landed: $16,150", ctx(LeadState.OFFER_MADE, ladder=LADDER)).ok
    r = validate("I can go to $17,500", ctx(LeadState.NEGOTIATING, ladder=LADDER))
    assert not r.ok and any("not on the authorised ladder" in v for v in r.violations)
    assert validate(
        "The offer stands at $17,100", ctx(LeadState.NEGOTIATING, ladder=LADDER, current_offer=17_100)
    ).ok


def test_commitment_competition_and_pressure_language():
    assert not validate("We guarantee to buy it, deal is done", ctx(LeadState.OFFER_MADE, ladder=LADDER)).ok
    assert not validate("We have other buyers lined up", ctx(LeadState.DISCOVERY)).ok
    assert not validate("Act now, last chance", ctx(LeadState.DISCOVERY)).ok
    assert validate(
        "Subject to inspection, open until Wednesday", ctx(LeadState.OFFER_MADE, ladder=LADDER)
    ).ok


def test_year_outside_fact_sheet_is_caught():
    assert not validate("Is it the 2021 model?", ctx(LeadState.DISCOVERY)).ok
    assert validate("Is it the 2019 model?", ctx(LeadState.DISCOVERY)).ok
    assert validate(
        "the listing said 2020 but the VIN shows 2019", ctx(LeadState.DISCOVERY, allowed_years={2020})
    ).ok
    assert validate(
        "open until 3pm on Wednesday 16 September 2026", ctx(LeadState.OFFER_MADE, ladder=LADDER)
    ).ok


def test_length_and_tone():
    long = "x" * 2001
    assert not validate(long, ctx(LeadState.DISCOVERY, max_length=2000)).ok
    assert not validate("GREAT CAR!! Send photos", ctx(LeadState.DISCOVERY)).ok
    assert validate("We run a PPSR check and need the VIN. LMCT 12345.", ctx(LeadState.DISCOVERY)).ok
    assert not validate("   ", ctx(LeadState.DISCOVERY)).ok
