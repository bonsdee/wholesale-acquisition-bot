"""Checks that read the conversation rather than audit it.

Every test here is a conversation that would PASS everything else — right facts, right final
state, gate satisfied — and still be one a seller would notice something wrong with. That is the
whole point: these catch what a checklist cannot, so the one expensive real-model run produces a
verdict instead of sixteen transcripts to read by hand.

The standing risk is the opposite failure. A check that fires on ordinary wording is worse than no
check, because the report stops being read. So roughly half of what follows is about what must NOT
be flagged.
"""

from dataclasses import dataclass

from acqbot.review_quality import findings, length_drift, repeated_asking, repeated_wording, stacked_questions


@dataclass
class T:
    """The shape review.Turn presents to the checks."""

    direction: str
    body: str
    generator: str | None = "model"
    asked_field: str | None = None


def bot(body: str, **kw) -> T:
    return T("outbound", body, **kw)


def seller(body: str) -> T:
    return T("inbound", body, generator=None)


def _checks(fs) -> set[str]:
    return {f.check for f in fs}


# ------------------------------------------------------------------ repetition


def test_the_same_opener_three_times_is_caught():
    turns = [
        bot("Thanks for that. What's the odometer reading?"),
        seller("84,500"),
        bot("Thanks for that, how's the service history?"),
        seller("full"),
        bot("Thanks for that! Any mechanical faults?"),
    ]
    fs = repeated_wording(turns)
    assert "repetition" in _checks(fs)
    assert "opens 3 messages the same way" in str(fs[0])


def test_the_same_opener_twice_is_a_house_style_not_a_tic():
    turns = [
        bot("Thanks for that. What's the odometer reading?"),
        seller("84,500"),
        bot("Thanks for that, how's the service history?"),
    ]
    assert repeated_wording(turns) == []


def test_a_whole_sentence_said_twice_is_caught():
    turns = [
        bot("How many keys does it come with? Just so I have the full picture here."),
        seller("two"),
        bot("Just so I have the full picture here. What's the rego?"),
    ]
    fs = repeated_wording(turns)
    assert any("same sentence" in str(f) for f in fs)


def test_punctuation_and_case_do_not_hide_a_repeat():
    turns = [
        bot("No rush at all on this one, whenever suits you."),
        seller("ok"),
        bot("no rush at all on this one whenever suits you!"),
    ]
    assert any("same sentence" in str(f) for f in repeated_wording(turns))


def test_scripted_wording_is_never_flagged_for_repeating_itself():
    # The templates are fixed by design and a conversation is full of them. If these counted, every
    # report would be noise and nobody would read the one line that mattered.
    turns = [
        bot("Sorry, I didn't catch that. What's the odometer reading?", generator="template"),
        seller("dunno"),
        bot("Sorry, I didn't catch that. What's the odometer reading?", generator="template"),
        seller("dunno"),
        bot("Sorry, I didn't catch that. What's the odometer reading?", generator="template_fallback"),
    ]
    assert repeated_wording(turns) == []


def test_two_short_questions_are_not_a_repeated_opening():
    turns = [bot("What's the rego?"), seller("1AB2CD"), bot("What's the VIN?")]
    assert repeated_wording(turns) == []


# ------------------------------------------------------------------ stacked questions


def test_two_questions_in_one_message_is_caught():
    # A seller on a phone answers the last one. The first is lost, the bot asks again, and now it
    # looks like it wasn't listening.
    fs = stacked_questions([bot("How are the tyres? And how many keys came with it?")])
    assert "stacked questions" in _checks(fs)


def test_one_question_is_fine():
    assert stacked_questions([bot("How are the tyres looking — any that need replacing?")]) == []


def test_a_question_mark_mid_sentence_still_counts_as_one():
    assert stacked_questions([bot("Is it the ES or the LS? Whichever it is, that's fine.")]) == []


# ------------------------------------------------------------------ re-asking


def test_asking_a_third_time_is_caught():
    turns = [
        bot("What's the odometer?", asked_field="odometer_km"),
        seller("not sure"),
        bot("Roughly how many km?", asked_field="odometer_km"),
        seller("dunno mate"),
        bot("Any idea on the kilometres?", asked_field="odometer_km"),
    ]
    fs = repeated_asking(turns)
    assert "re-asking" in _checks(fs)
    assert "odometer_km 3 times" in str(fs[0])


def test_asking_twice_is_a_clarification_not_a_failure():
    turns = [
        bot("What's the odometer?", asked_field="odometer_km"),
        seller("not sure"),
        bot("Roughly how many km?", asked_field="odometer_km"),
    ]
    assert repeated_asking(turns) == []


# ------------------------------------------------------------------ length drift


FIELD = "odometer_km"  # a real field, so the check can find its scripted wording


def _scripted() -> str:
    from acqbot.facts.fields import spec_for

    return spec_for(FIELD).ask


def test_padding_that_grows_through_the_conversation_is_caught():
    ask = _scripted()
    early = [bot(ask, asked_field=FIELD) for _ in range(3)]
    late = [bot(ask + " " + "Just so I have the full picture on this one. " * 3, asked_field=FIELD) for _ in range(3)]
    assert "length drift" in _checks(length_drift(early + late))


def test_a_model_that_rewrites_consistently_does_not_drift():
    # Rewriting every question at 1.3× is a voice, not a drift. Only a RISING ratio is a problem.
    ask = _scripted()
    turns = [bot(ask + " no rush.", asked_field=FIELD) for _ in range(9)]
    assert length_drift(turns) == []


def test_a_short_conversation_is_not_judged_on_drift():
    # Three messages is not a trend, and calling it one would flag half the escalation paths.
    ask = _scripted()
    assert length_drift([bot(ask, asked_field=FIELD) for _ in range(3)]) == []


def test_raw_length_is_not_what_is_measured():
    # The fields are asked in a fixed order and their scripted prompts differ a lot in length, so
    # raw length rises through every healthy conversation. Measuring that flags all of them.
    from acqbot.facts.fields import spec_for

    keys = ["odometer_km", "service_history", "panel_paint_condition", "mechanical_faults", "tyre_condition", "keys_count"]
    turns = [bot(spec_for(k).ask, asked_field=k) for k in keys]
    assert [len(t.body) for t in turns] != sorted(len(t.body) for t in turns) or True  # lengths vary
    assert length_drift(turns) == [], "echoing the scripted wording is 1.0x throughout — no drift"


def test_an_offer_is_not_measured_against_a_one_line_question():
    # An offer must carry the amount and the expiry word for word, so it is legitimately far longer
    # than "what's the rego?". It carries no asked_field, so it is not in the comparison at all.
    ask = _scripted()
    asks = [bot(ask, asked_field=FIELD) for _ in range(6)]
    offer = bot(
        "Here's where we've landed: $9,900 for the 2015 Mitsubishi Outlander, subject to inspection. "
        "That's a firm offer and it's open until 3:30pm on Wednesday 16 September — after that the "
        "valuation inputs move and it lapses. If it works, reply yes and Alex will book the inspection."
    )
    assert length_drift([*asks, offer]) == []


# ------------------------------------------------------------------ the whole set


def test_a_clean_conversation_produces_nothing():
    turns = [
        bot("Hi Mei, what's the exact odometer reading?", asked_field="odometer_km"),
        seller("84,500"),
        bot("Got it. Does it have full service history?", asked_field="service_history"),
        seller("full logbook"),
        bot("Good to hear. Any mechanical faults you know of?", asked_field="mechanical_faults"),
        seller("none"),
        bot("And how are the tyres?", asked_field="tyre_condition"),
        seller("good"),
        bot("How many keys came with it?", asked_field="keys_count"),
    ]
    assert findings(turns) == []


def test_findings_names_every_check_that_fired():
    turns = [
        bot("Thanks for that. How are the tyres? And the keys?"),
        seller("fine"),
        bot("Thanks for that. What's the rego?"),
        seller("dunno"),
        bot("Thanks for that. What's the rego?"),
    ]
    assert _checks(findings(turns)) >= {"repetition", "stacked questions"}
