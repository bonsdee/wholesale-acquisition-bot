from datetime import date

import pytest

from acqbot.conversation.extract import (
    extract,
    parse_identifier,
    parse_keys,
    parse_mechanical,
    parse_odometer,
    parse_panel,
    parse_rego_status,
    parse_service_history,
    parse_tyres,
    parse_yes_no_claim,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("84,500 km", 84_500),
        ("about 84k", 84_000),
        ("It's on 200,254 km right now", 200_254),
        ("112000", 112_000),
        ("12.5k", 12_500),
        ("not sure", None),
    ],
)
def test_odometer(text, expected):
    assert parse_odometer(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Full logbook, every service at the dealer", "full"),
        ("full", "full"),
        ("partial, missing a couple", "partial"),
        ("no history sorry", "none"),
        ("lost the logbook", "none"),
        ("hmm", None),
    ],
)
def test_service_history(text, expected):
    assert parse_service_history(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("No finance, paid it off years ago", False),
        ("nope", False),
        ("yes still owe about $8,000 to Toyota Finance", True),
        ("Never been written off", False),
        ("It was a repairable write off in 2019", True),
        ("maybe", None),
    ],
)
def test_yes_no_claims(text, expected):
    assert parse_yes_no_claim(text) == expected


def test_panel_grade_word_beats_volunteered_damage():
    """A seller who answers the grade question and then volunteers detail means the grade."""
    assert parse_panel("Good overall, a couple of small scratches on the rear bumper") == "good"
    assert parse_panel("good, small dent in the drivers door") == "good"
    assert parse_panel("fair, bumper is scuffed") == "fair"
    assert parse_panel("poor honestly, needs a respray") == "poor"
    # A body part on its own says nothing about condition.
    assert parse_panel("it has a bumper") is None
    assert parse_panel("rear bumper and both doors") is None


def test_panel_inferred_when_no_grade_stated():
    assert parse_panel("a few dents and scuffs down one side") == "fair"
    assert parse_panel("rust coming through the sills") == "poor"
    assert parse_panel("just a couple of stone chips") == "good"
    assert parse_panel("immaculate") == "excellent"
    assert parse_panel("hmm") is None


def test_panel_tyres_keys_mechanical():
    assert parse_panel("good overall, couple of small scratches") == "good"
    assert parse_panel("few dents and a scuffed bumper") == "fair"
    assert parse_panel("immaculate") == "excellent"
    assert parse_panel("rust on the sills") == "poor"
    assert parse_tyres("tyres are bald") == "replace"
    assert parse_tyres("new tyres last month") == "new"
    assert parse_tyres("getting a bit low") == "worn"
    assert parse_tyres("plenty of tread") == "good"
    assert parse_keys("two keys") == 2
    assert parse_keys("just the one") == 1
    assert parse_keys("2") == 2
    assert parse_mechanical("none") == {"none": True}
    assert parse_mechanical("no faults, drives perfectly") == {"none": True}
    m = parse_mechanical("check engine light comes on sometimes, and the aircon is weak")
    assert m["warning_lights"] is True and len(m["items"]) == 2


def test_rego_status():
    today = date(2026, 9, 14)
    assert parse_rego_status("unregistered", today=today) == {"status": "unregistered", "expiry": None}
    assert parse_rego_status("it expired last month", today=today)["status"] == "expired"
    r = parse_rego_status("yes current till March", today=today)
    assert r["status"] == "current" and r["expiry"] == "2027-03-01"
    r = parse_rego_status("registered until 11/26", today=today)
    assert r["expiry"] == "2026-11-01"


def test_identifiers():
    assert parse_identifier("Rego is 1AB2CD") == {"rego": "1AB2CD"}
    assert parse_identifier("1ab 2cd") == {"rego": "1AB2CD"}
    assert parse_identifier("VIN JHMGE8H50DC012345") == {"vin": "JHMGE8H50DC012345"}
    assert parse_identifier("its 84500 km") == {}
    assert parse_identifier("the CX5 GT") == {}


def test_extract_pending_field_and_flags():
    ex = extract("Full logbook", [], pending_field="service_history", stage_priced=False)
    assert ex.facts == {"service_history": "full"} and ex.parsed_pending

    ex = extract("Wait, is this a bot?", [], pending_field="odometer_km", stage_priced=False)
    assert "human_question" in ex.flags and not ex.facts

    ex = extract("My lawyer will hear about this", [], pending_field=None, stage_priced=False)
    assert "legal" in ex.flags
    ex = extract("It's my late father's car, I'm the executor", [], pending_field=None, stage_priced=False)
    assert "deceased_estate" in ex.flags
    ex = extract("it's in my mum's name actually", [], pending_field=None, stage_priced=False)
    assert "minor_or_no_authority" in ex.flags
    ex = extract("this is a scam, leave me alone", [], pending_field=None, stage_priced=False)
    assert "hostile" in ex.flags
    ex = extract("STOP", [], pending_field=None, stage_priced=False)
    assert "stop" in ex.flags


def test_extract_offer_intents():
    ex = extract("Yes, deal", [], pending_field=None, stage_priced=True)
    assert ex.intents == {"accept"}
    ex = extract("Too low, I want at least $22,500", [], pending_field=None, stage_priced=True)
    assert "counter" in ex.intents and ex.counter_price == 22_500 and "accept" not in ex.intents
    ex = extract("can you do a bit better?", [], pending_field=None, stage_priced=True)
    assert "counter" in ex.intents and ex.counter_price is None
    ex = extract("No thanks, not interested", [], pending_field=None, stage_priced=True)
    assert "reject" in ex.intents
    ex = extract("what would you pay for it?", [], pending_field=None, stage_priced=False)
    assert "price_question" in ex.intents
    # Before a price exists, "yes" is not an acceptance of anything.
    ex = extract("yes", [], pending_field=None, stage_priced=False)
    assert not ex.intents


def test_extract_photos_and_phone():
    ex = extract(
        "here you go, call me on 0412 345 678",
        [{"type": "image", "url": "https://x/1.jpg"}],
        pending_field="photos",
        stage_priced=False,
    )
    assert ex.photo_urls == ["https://x/1.jpg"] and ex.phone == "+61412345678"


def test_acceptance_survives_a_filler_word_in_front_of_it():
    # "Alright, done" ended a review run stuck in NEGOTIATING with the ladder exhausted.
    def intents(t):
        return extract(t, [], pending_field=None, stage_priced=True).intents

    for yes in ("Alright, done", "Ok then, deal", "Yeah, that'll do", "Great, sounds good", "done", "Yes"):
        assert "accept" in intents(yes), yes
    for no in ("yeah nah", "Alright, no thanks", "nah mate", "no deal"):
        assert "accept" not in intents(no), no
    assert "reject" in intents("yeah nah")


def test_selling_someone_elses_car_is_a_screen():
    # "it's my mum's car" is the commonest phrasing and was missed until the review harness ran it.
    def flags(t):
        return extract(t, [], pending_field=None, stage_priced=False).flags

    for text in (
        "Hi, it's my mum's car but I'm handling the sale for her",
        "it's my nan's car",
        "selling it for my brother",
        "the car belongs to my wife",
        "I have power of attorney for her",
    ):
        assert "minor_or_no_authority" in flags(text), text
    for text in (
        "my car is a 2016 Tucson",
        "I'm selling my car because we had a baby",
        "my wife drives it mostly",
        "my brother has the same car",
    ):
        assert "minor_or_no_authority" not in flags(text), text
