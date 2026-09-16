"""The scripted sellers again, through the Phase 4 path: every discovery message written by the
(fake) model, every inbound read by it, every call traced, every outbound gated, and nothing the
model does can move the stage, price the car, or leak a figure."""

import pytest
from sqlalchemy import select

from acqbot.conversation.gate import MONEY_RE
from acqbot.db import session_scope
from acqbot.demo import run_demo
from acqbot.llm.prompts import PROMPT_VERSION
from acqbot.models import LeadState, Message, ModelCall, Offer, StateLog, Valuation

DISCOVERY_TEMPLATES = {"clarify", "contradiction", "photos_partial", "verification_wait"}
# Phase 5: the model also words the offer messages. It still never chooses the figure — the gate
# pins that — and these are the only templates beyond discovery it is allowed anywhere near.
OFFER_TEMPLATES = {"offer", "concession", "offer_restate", "at_ceiling"}


def _discipline(run):
    with session_scope() as s:
        msgs = list(
            s.scalars(
                select(Message)
                .where(Message.lead_id == run.lead_id)
                .order_by(Message.sent_at, Message.msg_id)
            )
        )
        calls = list(
            s.scalars(select(ModelCall).where(ModelCall.lead_id == run.lead_id).order_by(ModelCall.at))
        )
        offers = list(s.scalars(select(Offer).where(Offer.lead_id == run.lead_id)))
        ladder_values = set()
        for o in offers:
            v = s.get(Valuation, o.valuation_id)
            ladder_values |= {int(round(x)) for x in v.ladder.values()}
            ladder_values.add(int(round(float(o.amount))))
        log = list(
            s.scalars(
                select(StateLog).where(StateLog.lead_id == run.lead_id).order_by(StateLog.at, StateLog.id)
            )
        )
    priced_at = next((h.at for h in log if h.to_state == LeadState.PRICED), None)
    inbound = [m for m in msgs if m.direction.value == "inbound" and m.lead_id]
    outbound = [m for m in msgs if m.direction.value == "outbound"]
    for m in outbound:
        notes = m.validation_notes
        assert m.validated is True and m.prompt_hash and notes["gate"]["ok"]
        template = notes["template"]
        written = template.startswith("ask:") or template in DISCOVERY_TEMPLATES | OFFER_TEMPLATES
        if written:
            assert notes["generator"] == "model" and m.model_version.endswith(f"|{PROMPT_VERSION}")
        else:
            assert notes["generator"] == "template" and m.model_version == "template:v1"
        figures = [int(x.replace(",", "")) for x in MONEY_RE.findall(m.body)]
        if priced_at is None or m.sent_at < priced_at:
            assert figures == [], f"figure before PRICED: {m.body}"
        else:
            assert all(f in ladder_values for f in figures)
    # One extraction per inbound that reached a linked lead; one generation per discovery outbound.
    extracts = [c for c in calls if c.purpose == "extract"]
    generates = [c for c in calls if c.purpose == "generate"]
    assert len(extracts) == len(inbound)
    assert len(generates) == sum(1 for m in outbound if m.validation_notes["generator"] == "model")
    for c in calls:
        assert c.error is None and c.response["parsed"] is not None
        system = c.request["system"]
        if c.purpose != "generate":
            assert "$" not in system
            continue
        assert c.request["messages"][-1]["role"] == "user"
        # The writer never sees the ladder it sits on, the engine's working, or the asking price.
        assert "ladder" not in system.lower() and "wholesale" not in system.lower()
        figures = {int(x.replace(",", "")) for x in MONEY_RE.findall(system)}
        if "Valuation: NOT RELEASED" in system:
            assert figures == set(), f"a figure reached a discovery prompt: {sorted(figures)}"
        else:
            # Presenting: exactly one figure, and it is one the valuation engine authorised.
            assert len(figures) == 1, f"the writer was given {len(figures)} figures: {sorted(figures)}"
            assert figures <= ladder_values


def test_happy_path_with_the_model_in_the_loop():
    run = run_demo("clean", "negotiate", seed=42, human_presents=False, model="fake")
    assert run.final_state == "HANDOFF"
    triggers = [h["trigger"] for h in run.state_log]
    assert "valuation_released" in triggers and triggers[-2:] == ["seller_accepted", "handoff_packet_written"]
    assert [o["step"] for o in run.offers] == ["opening", "step_1", "step_2"]
    generators = [m["generator"] for m in run.transcript if m["direction"] == "outbound"]
    assert generators[0] == "template" and "model" in generators  # opening scripted, discovery generated
    _discipline(run)


def test_model_mode_matches_template_mode_turn_for_turn():
    scripted = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    modelled = run_demo("clean", "accept", seed=42, human_presents=False, model="fake")
    assert scripted.final_state == modelled.final_state == "HANDOFF"
    assert [h["trigger"] for h in scripted.state_log] == [h["trigger"] for h in modelled.state_log]
    # The rule-backed fake writes exactly what the script would, so the transcripts agree too.
    assert [m["body"] for m in scripted.transcript] == [m["body"] for m in modelled.transcript]


def test_screens_and_handoff_work_the_same_with_the_model():
    run = run_demo("clean", "bot_question", seed=42, human_presents=False, model="fake")
    assert run.final_state == "HUMAN" and [t["reason"] for t in run.tasks] == ["human_requested"]
    run = run_demo("clean", "legal", seed=42, human_presents=False, model="fake")
    assert run.final_state == "HUMAN" and run.transcript[-1]["direction"] == "inbound"
    run = run_demo("contradiction", "accept", seed=3, human_presents=False, model="fake")
    assert run.final_state == "HANDOFF" and "contradicted_claims_present" in run.handoff["flags"]
    _discipline(run)


@pytest.mark.parametrize("seed", [1, 8, 13])
def test_discipline_across_seeds_with_the_model(seed):
    run = run_demo("clean", "negotiate", seed=seed, human_presents=False, model="fake")
    assert run.final_state in {"HANDOFF", "HUMAN", "TERMINATED"}
    _discipline(run)
