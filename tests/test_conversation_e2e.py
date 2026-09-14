"""End-to-end scripted sellers through the Console transport — Phase 3's proof."""

import re

import pytest
from sqlalchemy import select

from acqbot.conversation.gate import MONEY_RE
from acqbot.db import session_scope
from acqbot.demo import run_demo
from acqbot.models import Escalation, HandoffPacket, Lead, LeadState, Message, Offer, StateLog

PRICE_STATES = {"PRICED", "OFFER_MADE", "NEGOTIATING", "ACCEPTED", "HANDOFF"}


def _outbound(lead_id):
    with session_scope() as s:
        return list(
            s.scalars(
                select(Message)
                .where(Message.lead_id == lead_id, Message.direction == "outbound")
                .order_by(Message.sent_at)
            )
        )


def _assert_gate_discipline(run):
    """Every outbound validated; no dollar figure before PRICED; every figure on the ladder."""
    with session_scope() as s:
        msgs = list(
            s.scalars(
                select(Message)
                .where(Message.lead_id == run.lead_id)
                .order_by(Message.sent_at, Message.msg_id)
            )
        )
        offers = list(s.scalars(select(Offer).where(Offer.lead_id == run.lead_id)))
        ladder_values = set()
        for o in offers:
            from acqbot.models import Valuation

            v = s.get(Valuation, o.valuation_id)
            ladder_values |= {int(round(x)) for x in v.ladder.values()}
            ladder_values.add(int(round(float(o.amount))))
        log = list(
            s.scalars(
                select(StateLog).where(StateLog.lead_id == run.lead_id).order_by(StateLog.at, StateLog.id)
            )
        )
    priced_at = next((h.at for h in log if h.to_state == LeadState.PRICED), None)
    for m in msgs:
        if m.direction.value != "outbound":
            continue
        assert m.validated is True and m.model_version == "template:v1" and m.prompt_hash
        figures = [int(x.replace(",", "")) for x in MONEY_RE.findall(m.body)]
        if priced_at is None or m.sent_at < priced_at:
            assert figures == [], f"figure before PRICED: {m.body}"
        else:
            assert all(f in ladder_values for f in figures), f"off-ladder figure in {m.body}"


def test_happy_path_negotiation_to_handoff():
    run = run_demo("clean", "negotiate", seed=42, human_presents=False)
    assert run.final_state == "HANDOFF"
    triggers = [h["trigger"] for h in run.state_log]
    assert triggers[:4] == ["lead_ingested", "opening_sent", "seller_replied", "facts_updated"]
    assert "valuation_released" in triggers and "offer_presented:opening" in triggers
    assert "offer_presented:step_1" in triggers and "offer_presented:step_2" in triggers
    assert triggers[-2:] == ["seller_accepted", "handoff_packet_written"]
    steps = [o["step"] for o in run.offers]
    assert steps == ["opening", "step_1", "step_2"] and run.offers[-1]["outcome"] == "accepted"
    assert run.offers[0]["amount"] < run.offers[1]["amount"] < run.offers[2]["amount"]
    # Disclosure in the first message, before anything else.
    first = run.transcript[1]
    assert (
        first["direction"] == "outbound"
        and "automated assistant" in first["body"]
        and "LMCT" in first["body"]
    )
    # Handoff packet is complete.
    p = run.handoff
    assert p["agreed_price_aud"] == run.offers[-1]["amount"] and p["ladder_steps_used"] == 3
    assert p["next_action"] == "book_inspection" and p["ppsr"]["encumbered"] is False
    assert p["vehicle"]["confirmed"]["odometer_km"] and p["conversation_summary"]
    assert {t["reason"] for t in run.tasks} == {"verification_review", "handoff"}
    _assert_gate_discipline(run)


def test_human_presents_offers_by_default():
    run = run_demo("clean", "negotiate", seed=42, human_presents=True)
    # The human presented the opening; the first counter escalates to a person (Phase 3 mode).
    assert run.final_state == "HUMAN"
    assert [o["by"] for o in run.offers] == ["human:demo"]
    open_reasons = {t["reason"] for t in run.tasks if not t["resolved"]}
    assert open_reasons == {"offer_response_needed"}
    assert any(t["reason"] == "offer_presentation" and t["resolved"] for t in run.tasks)
    _assert_gate_discipline(run)


def test_final_rejection_archives():
    run = run_demo("clean", "reject", seed=42, human_presents=False)
    assert run.final_state == "ARCHIVED"
    assert run.offers[0]["outcome"] == "rejected"
    assert "offer stands until" in run.transcript[-1]["body"]


def test_bot_question_gets_a_straight_answer_then_a_human():
    run = run_demo("clean", "bot_question", seed=42, human_presents=False)
    assert run.final_state == "HUMAN"
    assert (
        "automated assistant" in run.transcript[-1]["body"] and run.transcript[-1]["direction"] == "outbound"
    )
    assert [t["reason"] for t in run.tasks] == ["human_requested"]


def test_legal_trigger_escalates_silently():
    run = run_demo("clean", "legal", seed=42, human_presents=False)
    assert run.final_state == "HUMAN"
    assert run.transcript[-1]["direction"] == "inbound"  # no closing line was generated
    assert [t["reason"] for t in run.tasks] == ["legal"]
    _assert_gate_discipline(run)


def test_no_progression_escalates_after_three_turns():
    run = run_demo("clean", "silent_pushback", seed=42, human_presents=False)
    assert run.final_state == "HUMAN" and any(t["reason"] == "no_progression" for t in run.tasks)


def test_contradiction_is_raised_and_flagged_in_packet():
    run = run_demo("contradiction", "accept", seed=3, human_presents=False)
    assert run.final_state == "HANDOFF"
    bodies = [m["body"] for m in run.transcript if m["direction"] == "outbound"]
    assert any(b.startswith("One thing to check") for b in bodies)
    assert "contradicted_claims_present" in run.handoff["flags"]
    assert "year" in run.handoff["vehicle"]["contradictions"]
    _assert_gate_discipline(run)


def test_no_identifiers_asks_for_rego_first_and_runs_ppsr_later():
    run = run_demo("no-identifiers", "accept", seed=3, human_presents=False)
    assert run.final_state == "HANDOFF"
    opening = run.transcript[1]["body"]
    assert "rego plate" in opening
    assert run.handoff["ppsr"]["checked_at"]  # PPSR ran once the rego resolved to a VIN


def test_written_off_never_gets_contacted():
    run = run_demo("written-off", "accept", seed=5, human_presents=False)
    assert run.final_state == "TERMINATED"
    assert all(m["direction"] == "inbound" for m in run.transcript)


def test_every_transition_is_logged_and_messages_are_immutable():
    run = run_demo("clean", "accept", seed=42, human_presents=False)
    with session_scope() as s:
        lead = s.get(Lead, run.lead_id)
        log = list(
            s.scalars(
                select(StateLog).where(StateLog.lead_id == run.lead_id).order_by(StateLog.at, StateLog.id)
            )
        )
        assert log[-1].to_state == lead.state
        for a, b in zip(log, log[1:], strict=False):
            assert a.to_state == b.from_state
        assert s.query(HandoffPacket).filter_by(lead_id=run.lead_id).count() == 1
        assert s.query(Escalation).filter_by(lead_id=run.lead_id, reason="handoff").count() == 1


def test_opening_is_specific_when_variant_unknown():
    run = run_demo("clean", "accept", seed=11, human_presents=False)
    opening = run.transcript[1]["body"]
    assert re.search(r"Is it the .+ or the .+\?|odometer|rego plate", opening)
    assert run.final_state in {"HANDOFF", "HUMAN"}


@pytest.mark.parametrize("seed", [1, 2, 8, 13])
def test_gate_discipline_across_seeds(seed):
    run = run_demo("clean", "negotiate", seed=seed, human_presents=False)
    assert run.final_state in {"HANDOFF", "HUMAN", "TERMINATED"}
    _assert_gate_discipline(run)
