"""Who the assistant says it is, now that the names are real people (21 Sep).

The named buyers are Direct Auto's salespeople. The assistant introduces itself as the dealership's
automated assistant working WITH one of them — never as them — and the seller meets the same name
at the inspection.
"""

import pytest
from pydantic import ValidationError

from acqbot.config import Settings
from acqbot.conversation import templates as T
from acqbot.demo import run_demo
from acqbot.llm.prompts import generation_system

ROSTER = ["Vikram", "Peter", "Simon", "Robin"]
IDENTITY = T.Identity(dealership="Direct Auto", lmct="0013130", agent="Vikram")


def test_the_default_roster_is_the_sales_team():
    assert Settings.model_fields["agent_names"].default_factory() == ROSTER
    # The GM and the interim COO are not named buyers.
    assert not {"Vic", "Winnie"} & set(Settings.model_fields["agent_names"].default_factory())


def test_the_opening_speaks_for_the_salesperson_not_as_them():
    body = T.opening(
        IDENTITY, "Sam Lee", {"year": 2019, "make": "Toyota", "model": "RAV4"}.get, "What's the rego?"
    )
    assert body.startswith(
        "Hi Sam, it's Direct Auto (LMCT 0013130). We're interested in buying your 2019 Toyota RAV4.\n\n"
        "I'm an automated assistant working with Vikram from our team, and you can ask for a person anytime."
    )
    assert "Vikram here" not in body
    assert body.endswith("\n\nWhat's the rego?")


def test_the_writer_is_told_it_works_with_the_salesperson_and_never_writes_as_them():
    system = generation_system(
        agent="Peter", dealership="Direct Auto", lmct="0013130", channel="messenger", max_length=600
    )
    assert system.startswith("You are the automated assistant for Direct Auto (LMCT 0013130)")
    assert "You work with Peter, a real person" in system
    assert "never as Peter" in system
    assert "You are Peter" not in system


@pytest.mark.parametrize("names", [[], [""], ["  "]])
def test_an_empty_roster_is_a_startup_error_not_an_invented_name(names):
    with pytest.raises(ValidationError):
        Settings(agent_names=names)


def test_names_are_trimmed_and_deduplicated():
    assert Settings(agent_names=[" Vikram ", "Peter", "Vikram"]).agent_names == ["Vikram", "Peter"]


def test_the_seller_hears_one_name_from_the_first_message_to_the_handover():
    run = run_demo("clean", "accept", seed=21, human_presents=False)
    opening = next(m["body"] for m in run.transcript if m["direction"] == "outbound")
    named = [n for n in ROSTER if f"working with {n} from our team" in opening]
    assert len(named) == 1, opening
    others = set(ROSTER) - set(named)
    outbound = [m["body"] or "" for m in run.transcript if m["direction"] == "outbound"]
    assert not any(n in body for body in outbound for n in others)
    assert run.final_state == "HANDOFF"
    assert f"{named[0]} will be in touch" in outbound[-1]


@pytest.mark.parametrize(
    ("given", "shown"), [("mobin", "Mobin"), ("MOBIN", "Mobin"), ("McKenzie Rae", "McKenzie"), ("", "there")]
)
def test_the_seller_is_greeted_by_name_as_a_person_would_write_it(given, shown):
    assert T.first_name(given) == shown
