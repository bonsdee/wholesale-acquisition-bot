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
    assert validate(
        "Odometer 84,500 km, two keys, 2019 model", ctx(LeadState.DISCOVERY, vehicle_odometer_km=84_500)
    ).ok


def test_generated_text_may_only_name_facts_in_the_sheet():
    # Section 7 gate: "no vehicle fact absent from the fact store".
    r = validate("So that's 92,000 km on the clock?", ctx(LeadState.DISCOVERY, vehicle_odometer_km=84_500))
    assert not r.ok and any("odometer figure" in v for v in r.violations)
    r = validate("Great, 84,500 km on the clock", ctx(LeadState.DISCOVERY))  # sheet has no odometer at all
    assert not r.ok and any("odometer figure" in v for v in r.violations)
    # A contradiction query may quote both values.
    assert validate(
        "The listing said 84,500 km but the photo shows 92,000 km — which is right?",
        ctx(LeadState.DISCOVERY, vehicle_odometer_km=92_000, allowed_odometers={84_500, 92_000}),
    ).ok
    r = validate("Nice Toyota — how many keys come with it?", ctx(LeadState.DISCOVERY, vehicle_make="Mazda"))
    assert not r.ok and any("vehicle make" in v for v in r.violations)
    assert validate("How many keys come with the Mazda?", ctx(LeadState.DISCOVERY, vehicle_make="Mazda")).ok
    assert validate(
        "How many keys come with the Mazda?", ctx(LeadState.DISCOVERY)
    ).ok  # make unknown: no check


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


def test_a_claim_that_this_is_the_best_figure_is_refused():
    # Verbatim from the first full real-model run, 21 Sep — said at step_2, with a human-approved
    # ceiling still above it. Untrue, and the conduct Section 1 exists to rule out.
    c = ctx(LeadState.NEGOTIATING, ladder=LADDER)
    said = (
        "hear you, Mei — here's $17,800, subject to inspection. it's not my call beyond this, but "
        "that's the best i can put forward."
    )
    r = validate(said, c)
    assert not r.ok and any("best, final or highest" in v for v in r.violations)
    for claim in (
        "That's our final offer.",
        "This is the best we can do.",
        "I can't go any higher than that.",
        "That's my top price.",
        "Take it or leave it.",
        "That's the most I've got room for.",  # the Phase 5 wording, in case it ever comes back
        "That's the limit.",
    ):
        assert not validate(claim, c).ok, claim


def test_saying_more_is_not_your_decision_is_true_and_passes():
    # The honest version of the same moment must never be refused — the scripted ceiling message
    # in particular, which goes through this gate like everything else.
    c = ctx(LeadState.NEGOTIATING, ladder=LADDER)
    for honest in (
        "$17,800 is as far as I can take it on my own. Going further isn't my call, so Alex will have a look.",
        "Going any higher isn't something I can decide — Alex will come back to you on it.",
        "I can go to $17,100 — same terms, subject to inspection.",
        "Let me know if you'd like to go ahead.",
    ):
        assert validate(honest, c).ok, (honest, validate(honest, c).violations)


def test_saying_there_is_no_room_is_refused_while_there_is_room():
    # Both verbatim from the second live run, 21 Sep — said at the FIRST concession, then followed
    # one message later by a higher figure. The model cannot tell step_1 from step_2; the gate can.
    step_1 = ctx(LeadState.NEGOTIATING, ladder=LADDER, sole_figure=LADDER["step_1"])
    for said in (
        "The figure Alex can offer is $17,100, subject to inspection. It's not something I can move beyond that.",
        "Fair enough, here's where we land: $17,100. If it's more than that, that's not something I can move on.",
        "I hear you. $17,100 is it — it's not my call to move it further.",
        "That's as far as I can go at $17,100.",
    ):
        r = validate(said, step_1)
        assert not r.ok and any("states a limit" in v for v in r.violations), said
    # The opening offer too: the automation has two steps still in hand.
    opening = ctx(LeadState.OFFER_MADE, ladder=LADDER, sole_figure=LADDER["opening"])
    assert not validate("$16,150 — and I can't go any higher than that.", opening).ok


def test_at_the_last_automated_step_not_my_decision_is_true_and_passes():
    step_2 = ctx(LeadState.NEGOTIATING, ladder=LADDER, sole_figure=LADDER["step_2"])
    for honest in (
        "I hear you, Mei. $17,800, subject to inspection. It's not my call to move it further.",
        "$17,800 is as far as I can take it on my own. Going further isn't my call, so Alex will have a look.",
    ):
        assert validate(honest, step_2).ok, (honest, validate(honest, step_2).violations)
    # ...but "best" or "final" is still refused even here: a person may approve more.
    assert not validate("$17,800 is our final offer.", step_2).ok


def test_ordinary_can_not_wording_is_not_mistaken_for_a_limit():
    step_1 = ctx(LeadState.NEGOTIATING, ladder=LADDER, sole_figure=LADDER["step_1"])
    for innocent in (
        "$17,100, subject to inspection, open until 3pm on Wednesday 23 September. Want to go ahead?",
        "I can't go into inspection timing myself — Alex books those. $17,100 stands.",
        "We can move on to booking the inspection whenever suits you. $17,100.",
    ):
        assert validate(innocent, step_1).ok, (innocent, validate(innocent, step_1).violations)


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


def test_gate_does_not_reject_ordinary_australian_wording():
    # False positives found while reviewing the prompts: these are all messages we WANT to send.
    assert validate("No hurry — send it through whenever suits.", ctx(LeadState.DISCOVERY)).ok
    assert validate("No rush at all on the photos.", ctx(LeadState.DISCOVERY)).ok
    assert validate("Is it still registered in VIC?", ctx(LeadState.DISCOVERY)).ok
    assert validate("What's the rego, or the VIN if it's handy?", ctx(LeadState.DISCOVERY)).ok
    # ...and these are still rejected.
    assert not validate("Hurry up, this is your last chance", ctx(LeadState.DISCOVERY)).ok
    assert not validate("Reply NOW PLEASE", ctx(LeadState.DISCOVERY)).ok


def test_the_cars_own_trim_level_is_not_shouting():
    # Found by watching the console: the opening message for a Tucson GLS was rejected by the gate
    # as shouting, because trim levels are three capitals and no fixed list can hold them all.
    bad = validate("We're interested in your 2019 Hyundai Tucson GLS.", ctx(LeadState.DISCOVERY))
    assert not bad.ok and any("shouting" in v for v in bad.violations)
    ok = validate(
        "We're interested in your 2019 Hyundai Tucson GLS.",
        ctx(LeadState.DISCOVERY, allowed_caps={"Hyundai", "Tucson", "GLS"}),
    )
    assert ok.ok, ok.violations
    # ...but a word that is not part of this car's name still is.
    assert not validate(
        "SEND THE PHOTOS", ctx(LeadState.DISCOVERY, allowed_caps={"Hyundai", "Tucson", "GLS"})
    ).ok


def test_an_offer_message_may_only_carry_the_one_figure_being_presented():
    # Phase 5. Told to present the opening, a model that writes the ceiling has written a figure
    # that IS on the ladder — so "on the ladder" is not a tight enough rule once it words offers.
    loose = ctx(LeadState.OFFER_MADE, ladder=LADDER)
    assert validate("We can do $18,350 for it", loose).ok  # the ceiling, waved through

    tight = ctx(LeadState.OFFER_MADE, ladder=LADDER, sole_figure=16_150)
    r = validate("We can do $18,350 for it", tight)
    assert not r.ok and any("other than the one being presented" in v for v in r.violations)
    assert validate("Here's where we've landed: $16,150", tight).ok


def test_the_amount_and_the_expiry_are_pinned_word_for_word():
    pinned = ctx(
        LeadState.OFFER_MADE,
        ladder=LADDER,
        sole_figure=16_150,
        must_include=["$16,150", "3:30pm on Friday 18 September"],
    )
    ok = validate(
        "We can do $16,150, subject to inspection. It's open until 3:30pm on Friday 18 September.",
        pinned,
    )
    assert ok.ok, ok.violations
    # Right number, invented deadline: the seller would be told something untrue.
    bad = validate("We can do $16,150, subject to inspection. Open until Friday.", pinned)
    assert not bad.ok and any("must state exactly" in v for v in bad.violations)
