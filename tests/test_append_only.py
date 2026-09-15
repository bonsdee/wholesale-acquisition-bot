"""The append-only invariant is enforced by the database, not by discipline."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from acqbot.conversation.transitions import escalate
from acqbot.facts.store import record_fact
from acqbot.ingestion.service import ingest_lead
from acqbot.models import (
    Channel,
    Direction,
    FactSource,
    LadderStep,
    Lead,
    Message,
    Offer,
    OfferOutcome,
    Thread,
    Valuation,
    ValuationBasis,
)
from acqbot.simulator import make_lead

# The triggers raise SQLSTATE 23000 with an "append-only" / "write-once" message. Drivers map that
# code to different DBAPI classes (psycopg: IntegrityError, pg8000: ProgrammingError), so match on
# the message rather than the class.
APPEND_ONLY = DBAPIError
APPEND_ONLY_MATCH = r"append-only|write-once"


def _lead(session, seed=40):
    lead_id = ingest_lead(session, make_lead("clean", seed=seed)).lead_id
    session.commit()
    return session.get(Lead, lead_id)


def _thread_and_message(session, lead):
    t = Thread(lead_id=lead.lead_id, channel=Channel.CONSOLE, external_id=f"console-{uuid.uuid4()}")
    session.add(t)
    session.flush()
    m = Message(thread_id=t.thread_id, lead_id=lead.lead_id, direction=Direction.INBOUND, body="hi")
    session.add(m)
    session.commit()
    return t, m


def test_messages_cannot_be_updated_or_deleted(session):
    lead = _lead(session)
    _, m = _thread_and_message(session, lead)
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(text("UPDATE messages SET body = 'edited' WHERE msg_id = :id"), {"id": m.msg_id})
    session.rollback()
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(text("DELETE FROM messages WHERE msg_id = :id"), {"id": m.msg_id})
    session.rollback()


def test_state_log_is_immutable(session):
    lead = _lead(session, seed=41)
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(text("DELETE FROM state_log WHERE lead_id = :id"), {"id": lead.lead_id})
    session.rollback()
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(text("UPDATE state_log SET trigger = 'x' WHERE lead_id = :id"), {"id": lead.lead_id})
    session.rollback()


def test_vehicle_facts_superseded_by_is_write_once(session):
    lead = _lead(session, seed=42)
    a = record_fact(session, lead.lead_id, "keys_count", 1, source=FactSource.SELLER, confidence=0.9).fact
    b = record_fact(session, lead.lead_id, "keys_count", 2, source=FactSource.INSPECTION, verified=True).fact
    session.commit()
    assert a.superseded_by == b.fact_id

    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):  # cannot re-point
        session.execute(
            text("UPDATE vehicle_facts SET superseded_by = :b WHERE fact_id = :a"),
            {"a": a.fact_id, "b": a.fact_id},
        )
    session.rollback()
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):  # cannot change the value
        session.execute(
            text("UPDATE vehicle_facts SET value = '3'::jsonb WHERE fact_id = :b"), {"b": b.fact_id}
        )
    session.rollback()
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):  # cannot delete
        session.execute(text("DELETE FROM vehicle_facts WHERE fact_id = :b"), {"b": b.fact_id})
    session.rollback()


def test_offer_outcome_is_write_once(session):
    lead = _lead(session, seed=43)
    v = Valuation(
        lead_id=lead.lead_id,
        basis=ValuationBasis.VERIFIED,
        engine_version="test",
        band_low=20000,
        band_high=22000,
        wholesale_max=21000,
        ladder={"opening": 18480},
        recon_estimate=0,
        recon_lines=[],
        inputs_snapshot={},
    )
    session.add(v)
    session.flush()
    o = Offer(
        lead_id=lead.lead_id,
        valuation_id=v.valuation_id,
        amount=18480,
        ladder_step=LadderStep.OPENING,
        presented_by="human:test",
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    session.add(o)
    session.commit()

    o.outcome = OfferOutcome.REJECTED
    o.outcome_at = datetime.now(UTC)
    session.commit()  # first resolution is fine

    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(
            text("UPDATE offers SET outcome = 'accepted' WHERE offer_id = :id"), {"id": o.offer_id}
        )
    session.rollback()
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(
            text("UPDATE valuations SET wholesale_max = 99999 WHERE valuation_id = :id"),
            {"id": v.valuation_id},
        )
    session.rollback()


def test_escalation_resolution_is_write_once(session):
    lead = _lead(session, seed=44)
    esc = escalate(session, lead, "test_reason", {"x": 1})
    session.commit()
    esc.resolved_by = "human:test"
    esc.resolution = "handled"
    esc.resolved_at = datetime.now(UTC)
    session.commit()
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(
            text("UPDATE escalations SET resolution = 'changed' WHERE escalation_id = :id"),
            {"id": esc.escalation_id},
        )
    session.rollback()
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(
            text("UPDATE escalations SET reason = 'other' WHERE escalation_id = :id"),
            {"id": esc.escalation_id},
        )
    session.rollback()


def test_model_calls_are_immutable(session):
    from acqbot.llm.client import ModelRequest, ModelResponse
    from acqbot.llm.trace import record_call

    lead = _lead(session, seed=44)
    req = ModelRequest(
        purpose="extract", model="fake", system="s", messages=[{"role": "user", "content": "x"}]
    )
    resp = ModelResponse(
        text="{}",
        parsed={},
        model="fake",
        stop_reason="end_turn",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
    )
    row = record_call(session, req, resp, prompt_version="prompt:test", lead_id=lead.lead_id, thread_id=None)
    session.commit()
    assert row.prompt_hash == req.prompt_hash("prompt:test") and len(row.prompt_hash) == 64
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(text("UPDATE model_calls SET error = 'x' WHERE call_id = :id"), {"id": row.call_id})
    session.rollback()
    with pytest.raises(APPEND_ONLY, match=APPEND_ONLY_MATCH):
        session.execute(text("DELETE FROM model_calls WHERE call_id = :id"), {"id": row.call_id})
    session.rollback()
