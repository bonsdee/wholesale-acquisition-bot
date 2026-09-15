"""Stage 2 with a model: the model proposes, code validates, the regex screens stay underneath."""

from datetime import UTC, datetime

from sqlalchemy import select

from acqbot.conversation.service import Conversation
from acqbot.db import session_scope
from acqbot.facts.store import fact_sheet
from acqbot.ingestion.service import ingest_lead
from acqbot.llm.client import ModelError
from acqbot.llm.fake import ScriptedFakeClient
from acqbot.models import Escalation, Lead, Message, ModelCall
from acqbot.queue.worker import drain
from acqbot.simulator import make_lead
from acqbot.transport.console import ConsoleTransport
from acqbot.transport.registry import reset_console_transport

EMPTY = {
    "facts": [],
    "answered_pending": False,
    "intents": [],
    "flags": [],
    "counter_price_aud": None,
    "phone": None,
    "seller_question": None,
    "notes": [],
}


def extraction(**over):
    return {**EMPTY, **over}


def open_thread(client, *, scenario="clean", seed=42) -> tuple[ConsoleTransport, str, object]:
    """Ingest a lead, enrich it, and have the seller open the thread. Returns (console, seller_id, lead_id)."""
    console = reset_console_transport()
    payload = make_lead(scenario, seed=seed)
    seller_id = f"seller-{seed}"
    payload["seller"]["platform_id"] = seller_id
    with session_scope() as s:
        lead_id = ingest_lead(s, payload).lead_id
    drain("test")
    say(console, client, seller_id, "Hi, saw you're keen on my car", ref=str(lead_id))
    return console, seller_id, lead_id


def say(console, client, seller_id, text, *, ref=None, attachments=None):
    console.inject(seller_id, text, referral_ref=ref, attachments=attachments)
    results = []
    for msg in console.poll(datetime(2000, 1, 1, tzinfo=UTC)):
        with session_scope() as s:
            results.append(Conversation(s, console, model=client).handle_inbound(msg))
    drain("test")
    return results[-1]


def asked(lead_id):
    with session_scope() as s:
        last = s.scalars(
            select(Message)
            .where(Message.lead_id == lead_id, Message.direction == "outbound")
            .order_by(Message.sent_at.desc(), Message.msg_id.desc())
        ).first()
        return (last.validation_notes or {}).get("asked_field"), last.body, dict(last.validation_notes or {})


def test_model_facts_are_recorded_after_coercion_and_multiple_fields_per_message_count():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    assert asked(lead_id)[0] == "odometer_km"
    client.queue(
        "extract",
        extraction(
            facts=[
                {"field": "odometer_km", "value": "200,600", "confidence": 1.0},
                {"field": "service_history", "value": "full", "confidence": 0.95},
                {"field": "keys_count", "value": "2", "confidence": 1.0},
                {"field": "tyre_condition", "value": "bald", "confidence": 1.0},  # not in the vocabulary
            ],
            answered_pending=True,
            notes=["second owner", "timing belt done at 150k"],
        ),
    )
    r = say(console, client, seller_id, "200,600 on the clock, full logbook, two keys, tyres are bald")
    assert set(r.facts_recorded) == {"odometer_km", "service_history", "keys_count"}
    with session_scope() as s:
        sheet = fact_sheet(s, lead_id)
        assert sheet.get("odometer_km") == 200600 and sheet.get("keys_count") == 2
        assert sheet.get("tyre_condition") is None
        assert sheet.get("seller_notes") == ["second owner", "timing belt done at 150k"]
        calls = list(s.scalars(select(ModelCall).where(ModelCall.lead_id == lead_id)))
        assert {c.purpose for c in calls} >= {"extract", "generate"}
        assert all(c.prompt_hash and c.request["system"] for c in calls)
    # The next question skips the fields the seller already volunteered.
    assert asked(lead_id)[0] == "panel_paint_condition"


def test_low_confidence_answer_is_not_recorded_and_is_asked_again():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    with session_scope() as s:
        listed = fact_sheet(s, lead_id).get("odometer_km")
    client.queue(
        "extract",
        extraction(
            facts=[{"field": "odometer_km", "value": "200000", "confidence": 0.4}], answered_pending=True
        ),
    )
    r = say(console, client, seller_id, "somewhere around 200 I think, maybe more")
    assert r.facts_recorded == []
    field, _, notes = asked(lead_id)
    assert field == "odometer_km" and notes["template"] == "clarify"
    with session_scope() as s:
        assert fact_sheet(s, lead_id).get("odometer_km") == listed  # the listing value still stands
        gen = s.scalars(
            select(ModelCall)
            .where(ModelCall.lead_id == lead_id, ModelCall.purpose == "generate")
            .order_by(ModelCall.at.desc())
        ).first()
        assert "too vague to record" in gen.request["system"]


def test_regex_screens_are_a_floor_under_the_model():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue("extract", extraction())  # the model sees nothing wrong
    r = say(console, client, seller_id, "My lawyer says I should take you to consumer affairs")
    assert r.action == "escalated" and r.escalated == "legal"


def test_model_flag_the_regex_missed_still_escalates():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue("extract", extraction(flags=["distress"]))
    r = say(console, client, seller_id, "everything is falling apart here, I just need this gone")
    assert r.action == "escalated" and r.escalated == "distress"
    with session_scope() as s:
        assert s.get(Lead, lead_id).state.value == "HUMAN"


def test_model_failure_falls_back_to_the_regex_parser_for_that_turn():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue("extract", ModelError("boom", retryable=True))
    r = say(console, client, seller_id, "It's on 200,600 km right now")
    assert r.facts_recorded == ["odometer_km"]
    with session_scope() as s:
        failed = s.scalars(
            select(ModelCall).where(ModelCall.lead_id == lead_id, ModelCall.error.is_not(None))
        )
        assert [c.purpose for c in failed] == ["extract"]


def test_deferred_field_moves_to_the_back_of_the_queue_then_comes_back():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue("extract", extraction(intents=["defer"]))
    r = say(console, client, seller_id, "I'll have to check the dash tonight")
    assert r.action == "replied"
    field, _, notes = asked(lead_id)
    assert field == "service_history" and notes["template"] == "ask:service_history"
    with session_scope() as s:
        assert fact_sheet(s, lead_id).get("deferred_fields") == ["odometer_km"]
        gen = s.scalars(
            select(ModelCall)
            .where(ModelCall.lead_id == lead_id, ModelCall.purpose == "generate")
            .order_by(ModelCall.at.desc())
        ).first()
        assert "come back to you on confirmed odometer" in gen.request["system"]
    # Everything else answered → the deferred field is asked again at the end.
    for text in [
        "Full logbook",
        "Good, couple of scratches",
        "None",
        "Tyres are good",
        "Two keys",
    ]:
        say(console, client, seller_id, text)
    photos = [{"type": "image", "url": f"https://example.invalid/{i}.jpg"} for i in range(8)]
    say(console, client, seller_id, "Here you go", attachments=photos)
    assert asked(lead_id)[0] == "odometer_km"


def test_seller_question_is_passed_to_the_writer_and_does_not_count_as_progress():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue("extract", extraction(intents=["question"], seller_question="why do you need the odometer?"))
    say(console, client, seller_id, "why do you need that?")
    field, _, notes = asked(lead_id)
    assert field == "odometer_km" and notes["template"] == "clarify"
    with session_scope() as s:
        gen = s.scalars(
            select(ModelCall)
            .where(ModelCall.lead_id == lead_id, ModelCall.purpose == "generate")
            .order_by(ModelCall.at.desc())
        ).first()
        assert 'They also asked: "why do you need the odometer?"' in gen.request["system"]
        assert not list(s.scalars(select(Escalation).where(Escalation.lead_id == lead_id)))
