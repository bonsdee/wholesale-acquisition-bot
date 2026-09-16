"""Phase 5 — the automation presents offers, and the three things that go wrong when it does.

The spec's Phase 5 is one line: "Automated offer presentation. Ladder released to the model under
the constraints in Section 6.4. Human approval on all escalations and anything at floor." These
tests are the constraints, plus the two deadlines that exist in the spec and would otherwise exist
only on paper: the 48-hour offer expiry and the handoff SLA.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from acqbot.db import session_scope
from acqbot.demo import run_demo
from acqbot.models import Escalation, HandoffPacket, Job, LadderStep, Lead, Message, Offer, OfferOutcome
from acqbot.queue.jobs import enqueue
from acqbot.queue.worker import drain


def _offers(lead_id):
    with session_scope() as s:
        return list(s.scalars(select(Offer).where(Offer.lead_id == lead_id).order_by(Offer.presented_at)))


def _open_reasons(lead_id):
    with session_scope() as s:
        return [
            e.reason
            for e in s.scalars(
                select(Escalation).where(Escalation.lead_id == lead_id, Escalation.resolved_at.is_(None))
            )
        ]


def _outbound(lead_id):
    with session_scope() as s:
        return list(
            s.scalars(
                select(Message)
                .where(Message.lead_id == lead_id, Message.direction == "outbound")
                .order_by(Message.sent_at)
            )
        )


# ------------------------------------------------------------------ the ladder, walked


def test_automation_walks_the_two_authorised_steps_and_stops():
    run = run_demo("clean", "negotiate", seed=42, human_presents=False, model="off")
    steps = [o.ladder_step for o in _offers(run.lead_id)]
    assert steps[0] == LadderStep.OPENING
    assert LadderStep.FLOOR not in steps, "automation presented the ceiling"
    assert all(s != LadderStep.HUMAN for s in steps)
    assert steps == sorted(steps, key=[LadderStep.OPENING, LadderStep.STEP_1, LadderStep.STEP_2].index)


def test_the_ceiling_is_asked_for_not_taken():
    # A seller who keeps countering runs the automated ladder out. What happens then is the whole
    # question Phase 5 has to answer, and the answer is: it asks, with the sum already done.
    run = run_demo("clean", "haggle_to_ceiling", seed=42, human_presents=False, model="off")
    steps = [o.ladder_step for o in _offers(run.lead_id)]
    assert LadderStep.FLOOR not in steps
    reasons = _open_reasons(run.lead_id)
    assert "ceiling_approval" in reasons or "above_authorised_ladder" in reasons
    with session_scope() as s:
        e = s.scalars(
            select(Escalation).where(
                Escalation.lead_id == run.lead_id,
                Escalation.reason.in_(["ceiling_approval", "above_authorised_ladder"]),
            )
        ).first()
        # The person is told the figure, not asked to work it out.
        assert e.details["ceiling"] and e.details["amount"]
        assert s.get(Lead, run.lead_id).state.value == "HUMAN"


def test_a_person_can_approve_the_ceiling_and_the_automation_cannot():
    run = run_demo("clean", "haggle_to_ceiling", seed=42, human_presents=False, model="off")
    from acqbot.api import admin

    with session_scope() as s:
        assert s.get(Lead, run.lead_id).state.value == "HUMAN"

    body = admin.PresentOffer(step=LadderStep.FLOOR, by="claudia", amount_aud=None)
    out = admin.present_offer(run.lead_id, body)
    assert out["step"] == "floor" and out["state"] == "OFFER_MADE"

    with session_scope() as s:
        last = s.scalars(
            select(Offer).where(Offer.lead_id == run.lead_id).order_by(Offer.presented_at.desc())
        ).first()
        assert last.presented_by == "human:claudia" and last.ladder_step == LadderStep.FLOOR
    # Approving the ceiling answers the question that was asked.
    assert "ceiling_approval" not in _open_reasons(run.lead_id)


# ------------------------------------------------------------------ the 48 hours


def test_an_expired_offer_is_said_out_loud_and_never_silently_re_offered(settings_env):
    # The clock cannot be wound forward afterwards — `offers` rows are append-only and the trigger
    # refuses — so the offer is given a zero-hour life at the moment it is made instead.
    settings_env(offer_expiry_hours=0)
    run = run_demo("clean", "silent_pushback", seed=42, human_presents=True, model="off")
    from acqbot.api import admin

    before = len(_outbound(run.lead_id))
    offer_id = uuid.UUID(
        admin.present_offer(
            run.lead_id, admin.PresentOffer(step=LadderStep.OPENING, by="claudia", amount_aud=None)
        )["offer_id"]
    )
    drain("test")

    with session_scope() as s:
        assert s.get(Offer, offer_id).outcome == OfferOutcome.EXPIRED
    msgs = _outbound(run.lead_id)
    assert len(msgs) == before + 2, "the offer went out, then nothing told the seller it lapsed"
    lapse = msgs[-1]
    assert "lapsed" in lapse.body and "$" not in lapse.body  # a dead number is not restated
    assert lapse.validation_notes["gate"]["ok"]
    assert "offer_expired" in _open_reasons(run.lead_id)
    # And no replacement offer appeared on its own — that would make the deadline a lie.
    assert all(o.presented_at <= lapse.sent_at for o in _offers(run.lead_id))


# ------------------------------------------------------------------ the handoff SLA


def test_an_unclaimed_deal_packet_goes_back_on_the_queue(settings_env):
    settings_env(human_sla_hours=0)  # packets are append-only too: born expired, not aged
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    with session_scope() as s:
        packet_id = (
            s.scalars(select(HandoffPacket).where(HandoffPacket.lead_id == run.lead_id)).one().packet_id
        )
        assert list(s.scalars(select(Job).where(Job.kind == "handoff_sla"))), "no SLA alarm was set"
    drain("test")
    assert "handoff_sla_expired" in _open_reasons(run.lead_id)
    with session_scope() as s:
        e = s.scalars(
            select(Escalation).where(
                Escalation.lead_id == run.lead_id, Escalation.reason == "handoff_sla_expired"
            )
        ).one()
        assert e.details["packet_id"] == str(packet_id)
        assert e.details["agreed_price_aud"]


def test_a_claimed_packet_does_not_come_back():
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    with session_scope() as s:
        packet = s.scalars(select(HandoffPacket).where(HandoffPacket.lead_id == run.lead_id)).one()
        packet.claimed_by, packet.claimed_at = "daniel", datetime.now(UTC)  # the two mutable columns
        packet_id = packet.packet_id
    # Fire the alarm anyway: someone picked it up, so it has nothing to say.
    with session_scope() as s:
        enqueue(s, "handoff_sla", {"packet_id": str(packet_id)}, dedupe_key=f"sla-retest:{packet_id}")
    drain("test")
    assert "handoff_sla_expired" not in _open_reasons(run.lead_id)


# ------------------------------------------------------------------ what we say, and whether it is true


def test_a_concession_never_claims_to_be_the_last_one():
    # Found by watching the automation walk the ladder: the step_1 script said "that's the most I've
    # got room for" and then the bot offered more twice. True at step_2, a lie at step_1, and the
    # seller cannot tell which one they are reading.
    from acqbot.conversation import templates as T

    body = T.concession(5_950, datetime.now(UTC) + timedelta(hours=48), "Australia/Melbourne")
    for claim in ("most I've got", "best I can", "as high as", "final", "last offer"):
        assert claim not in body.lower(), f"a concession claimed finality: {claim}"


def test_the_at_ceiling_message_does_not_call_itself_the_ceiling():
    # step_2 is where automation stops, not where the dealership stops — a person may approve the
    # real ceiling minutes later, and then the previous message was untrue.
    from acqbot.conversation import templates as T

    body = T.at_ceiling(6_200, T.Identity(agent="Alex", dealership="Placeholder Motors", lmct="00000"))
    assert "ceiling" not in body.lower() and "can't go past" not in body.lower()
    assert "Alex" in body


def test_the_ladder_wording_differs_at_every_step(settings_env):
    settings_env(llm_provider="off")
    run = run_demo("clean", "haggle_to_ceiling", seed=42, human_presents=False, model="off")
    bodies = [m.body for m in _outbound(run.lead_id)]
    priced = [b for b in bodies if "$" in b]
    assert len(priced) >= 3
    assert len(set(priced)) == len(priced), "two offer messages were word-for-word identical"
