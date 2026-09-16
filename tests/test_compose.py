"""Stages 4–6 with a model: the writer is behind the gate, the script is behind the writer."""

from sqlalchemy import select

from acqbot.conversation.compose import MODEL_DIRECTIVES
from acqbot.conversation.gate import MONEY_RE
from acqbot.db import session_scope
from acqbot.llm.client import ModelError
from acqbot.llm.fake import ScriptedFakeClient
from acqbot.llm.prompts import PROMPT_VERSION, turn_context
from acqbot.models import Escalation, Lead, Message, ModelCall
from test_extract_model import asked, extraction, open_thread, say


def draft(message, **over):
    return {
        "message": message,
        "proposed_state": "DISCOVERY",
        "confidence": 0.9,
        "escalate": False,
        "escalate_reason": None,
        **over,
    }


def last_outbound(lead_id):
    with session_scope() as s:
        return s.scalars(
            select(Message)
            .where(Message.lead_id == lead_id, Message.direction == "outbound")
            .order_by(Message.sent_at.desc(), Message.msg_id.desc())
        ).first()


def test_gate_rejection_is_fed_back_once_then_the_rewrite_goes_out():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue("extract", extraction(facts=[{"field": "odometer_km", "value": "200600", "confidence": 1}]))
    client.queue(
        "generate",
        draft("Nice, 200,600 km is fair for the age — we'd be around $6,000. Service history?"),
        draft("Thanks for that. Do you have the service history — full logbook, partial, or none?"),
    )
    say(console, client, seller_id, "200,600")
    m = last_outbound(lead_id)
    assert m.body.startswith("Thanks for that.")
    notes = m.validation_notes
    assert notes["generator"] == "model" and len(notes["attempts"]) == 2
    assert any("figure before PRICED" in v for v in notes["attempts"][0]["gate"]["violations"])
    assert notes["attempts"][1]["gate"]["ok"] is True
    assert m.model_version == f"fake:scripted|{PROMPT_VERSION}"
    # The rewrite request carried the violations back to the model.
    second = [r for r in client.requests if r.purpose == "generate"][-1]
    assert "rejected by the validation gate" in second.messages[-1]["content"]
    assert second.messages[-2]["role"] == "assistant" and "$6,000" in second.messages[-2]["content"]
    # And the message's prompt_hash points at the trace row that produced it.
    with session_scope() as s:
        row = s.scalars(select(ModelCall).where(ModelCall.prompt_hash == m.prompt_hash)).first()
        assert row is not None and row.purpose == "generate" and row.response["parsed"]["message"] == m.body


def test_two_bad_drafts_fall_back_to_the_script():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue("extract", extraction(facts=[{"field": "odometer_km", "value": "200600", "confidence": 1}]))
    client.queue(
        "generate",
        draft("Other buyers are circling, so act now — service history?"),
        draft("We guarantee to buy it!! Service history?"),
    )
    say(console, client, seller_id, "200,600")
    m = last_outbound(lead_id)
    assert m.body == "Thanks. Service history — full logbook, partial, or none?"
    assert m.validation_notes["generator"] == "template_fallback"
    assert m.validation_notes["fallback"] == "ask:service_history"
    assert m.model_version == "template:v1" and m.validated is True


def test_model_call_failure_falls_back_to_the_script():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue("generate", ModelError("rate limited", retryable=True))
    say(console, client, seller_id, "200,600")
    m = last_outbound(lead_id)
    assert m.validation_notes["generator"] == "template_fallback"
    assert m.validation_notes["attempts"][0]["error"] == "rate limited"


def test_model_escalation_discards_the_message_and_routes_to_a_human():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue("generate", draft("", escalate=True, escalate_reason="minor_or_no_authority"))
    before = last_outbound(lead_id).msg_id
    # Deliberately a phrasing the regex screens do NOT catch, so the model's own flag is the only
    # thing standing between this seller and a discovery question.
    r = say(console, client, seller_id, "my parents bought it for me but I am the one using it")
    assert r.action == "escalated" and r.escalated == "model_flagged:minor_or_no_authority"
    assert last_outbound(lead_id).msg_id == before  # nothing went out
    with session_scope() as s:
        assert s.get(Lead, lead_id).state.value == "HUMAN"
        esc = s.scalars(select(Escalation).where(Escalation.lead_id == lead_id)).one()
        assert esc.reason == "model_flagged:minor_or_no_authority" and esc.details["directive"] == "clarify"


def test_the_writer_writes_discovery_and_offers_and_nothing_else():
    assert MODEL_DIRECTIVES == {
        "ask_field",
        "clarify",
        "contradiction",
        "photos_partial",
        "verification_wait",
        "offer",
        "concession",
        "offer_restate",
        "at_ceiling",
    }


def test_the_writer_is_given_one_figure_at_most_and_never_the_ladder():
    kw = dict(
        outstanding=["service_history"],
        sheet={"confirmed": {"make": "Mazda"}, "claimed": {"odometer_km": 84500}, "contradicted": {}},
        seller_first_name="Jo",
        channel="messenger",
        max_length=600,
    )
    before = turn_context(stage="DISCOVERY", instruction="Ask for service history.", **kw)
    assert "NOT RELEASED" in before and "$" not in before and "ladder" not in before.lower()

    # At PRICED it is handed the single amount it must present — and still not the ladder it sits on,
    # because a writer that knows the ceiling is a writer that can hint at it.
    presenting = turn_context(stage="OFFER_MADE", instruction="Present it.", offer_amount="$16,150", **kw)
    assert "NOT RELEASED" not in presenting
    assert MONEY_RE.findall(presenting) == ["16,150"]
    assert "ladder" not in presenting.lower() and "ceiling" in presenting.lower()

    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    say(console, client, seller_id, "200,600")
    for req in client.requests:
        if req.purpose == "generate":
            assert "$" not in req.system and "ladder" not in req.system.lower()
            assert "Valuation: NOT RELEASED" in req.system


def test_state_disagreement_is_logged_not_acted_on():
    client = ScriptedFakeClient()
    console, seller_id, lead_id = open_thread(client)
    client.queue(
        "generate",
        draft("Do you have the service history — full, partial, or none?", proposed_state="PRICED"),
    )
    say(console, client, seller_id, "200,600")
    m = last_outbound(lead_id)
    assert m.validation_notes["attempts"][0]["state_disagreement"] == "model PRICED vs machine DISCOVERY"
    with session_scope() as s:
        assert s.get(Lead, lead_id).state.value == "DISCOVERY"
    assert asked(lead_id)[0] == "service_history"


def test_context_hides_urls_phone_and_the_asking_price():
    from acqbot.llm.prompts import context_view

    view = context_view(
        {
            "confirmed": {
                "make": "Mazda",
                "photos": ["u1", "u2", "u3"],
                "finance_owing": {"owing": False, "checked_at": "t"},
            },
            "claimed": {
                "asking_price_aud": 9300,
                "listing_images": ["l1"],
                "seller_phone": "+61400000000",
                "keys_count": 2,
            },
            "contradicted": {},
        }
    )
    assert view["confirmed"] == {"make": "Mazda", "photos_received": 3, "finance_owing": {"owing": False}}
    assert view["claimed"] == {"keys_count": 2}
    text = turn_context(
        stage="DISCOVERY",
        outstanding=[],
        sheet={"confirmed": {}, "claimed": {"asking_price_aud": 9300}, "contradicted": {}},
        instruction="x",
        seller_first_name="",
        channel="messenger",
        max_length=600,
    )
    assert "9300" not in text and "asking" not in text
