"""First contact by SMS, for a dealership with Twilio and no Messenger (first_contact=sms).

The conversation itself is channel-blind (4.3); what these tests pin is the start — the one place
the code used to assume the seller would message us first — and that the rest then runs unchanged.
"""

import uuid

import pytest
from sqlalchemy import select

from acqbot.db import session_scope
from acqbot.demo import ANSWERS
from acqbot.facts.store import fact_sheet
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Channel, Escalation, Lead, LeadState, Message, Thread
from acqbot.queue.jobs import enqueue
from acqbot.queue.worker import drain
from acqbot.simulator import make_lead
from acqbot.transport.console import ConsoleTransport
from acqbot.transport.protocol import InboundMessage

PHONE = "+61412345678"


class FakeSms(ConsoleTransport):
    """Twilio without the network: records what was sent, on the SMS channel."""

    channel = Channel.SMS

    @property
    def max_body_length(self) -> int:
        return 1000


@pytest.fixture
def sms(settings_env, monkeypatch):
    settings_env(first_contact="sms")
    fake = FakeSms()
    monkeypatch.setattr("acqbot.transport.registry.get_transport", lambda channel: fake)
    return fake


def _new_lead(seed: int, phone: str | None = "0412 345 678") -> uuid.UUID:
    payload = make_lead("clean", seed=seed)
    payload["seller"]["phone"] = phone
    with session_scope() as s:
        lead_id = ingest_lead(s, payload).lead_id
    drain("test")  # enrichment, then maybe_send_opening
    return lead_id


def _reply(text: str, *, photos: int = 0) -> None:
    """A seller's text arriving the way the Twilio webhook delivers it: queued, then worked."""
    msg = InboundMessage(
        channel=Channel.SMS,
        external_id=PHONE,
        body=text,
        external_msg_id=f"SM{uuid.uuid4().hex}",
        attachments=[{"type": "image", "url": f"https://x/p{i}.jpg"} for i in range(photos)],
    )
    with session_scope() as s:
        enqueue(s, "inbound_message", msg.to_payload())
    drain("test")


def _state(lead_id: uuid.UUID) -> LeadState:
    with session_scope() as s:
        return s.get(Lead, lead_id).state


def _asked(lead_id: uuid.UUID) -> str | None:
    with session_scope() as s:
        last = s.scalars(
            select(Message)
            .where(Message.lead_id == lead_id, Message.direction == "outbound")
            .order_by(Message.sent_at.desc())
        ).first()
        return (last.validation_notes or {}).get("asked_field") if last else None


def test_a_new_lead_with_a_phone_gets_a_text_first(sms):
    lead_id = _new_lead(31)
    opening = sms.last_sent(PHONE)
    assert opening, "nothing was texted"
    assert "automated assistant" in opening  # Section 8 disclosure
    assert "listed for sale" in opening  # why a stranger has their number
    assert "Reply STOP" in opening  # Spam Act 2003 opt-out
    assert _state(lead_id) == LeadState.CONTACTED
    with session_scope() as s:
        thread = s.scalars(select(Thread).where(Thread.lead_id == lead_id)).one()
        assert (thread.channel, thread.external_id) == (Channel.SMS, PHONE)
        assert fact_sheet(s, lead_id).get("seller_phone") == PHONE


def test_the_whole_deal_runs_over_sms_and_never_asks_for_a_mobile(sms):
    lead_id = _new_lead(32)
    with session_scope() as s:
        sheet = fact_sheet(s, lead_id)
    fill = {"odometer": sheet.get("odometer_km"), "rego": sheet.get("rego") or "ABC123", "variant": ""}

    for _ in range(25):  # bounded: a stuck loop fails here instead of hanging
        state = _state(lead_id)
        if state not in {LeadState.CONTACTED, LeadState.ENGAGED, LeadState.DISCOVERY}:
            break
        field = _asked(lead_id)
        if field == "photos":
            _reply("Here you go", photos=5)
        else:
            _reply(ANSWERS.get(field or "", "Yes it is").format(**fill))
    else:
        pytest.fail(f"discovery never finished; stuck on {_asked(lead_id)}")

    assert _state(lead_id) in {LeadState.VERIFICATION, LeadState.PRICED, LeadState.OFFER_MADE}
    sent = [body for _, body in sms.outbox]
    assert not any("mobile" in b.lower() for b in sent), "asked an SMS seller for their mobile"
    assert all(ext == PHONE for ext, _ in sms.outbox)


def test_messenger_mode_is_unchanged_and_waits_for_the_seller(settings_env, monkeypatch):
    fake = FakeSms()
    monkeypatch.setattr("acqbot.transport.registry.get_transport", lambda channel: fake)
    settings_env(first_contact="messenger")
    lead_id = _new_lead(33)
    assert fake.outbox == [] and _state(lead_id) == LeadState.NEW


def test_no_phone_on_the_listing_means_no_text(sms):
    lead_id = _new_lead(34, phone=None)
    assert sms.outbox == [] and _state(lead_id) == LeadState.NEW


def test_a_number_already_in_another_chat_goes_to_a_person_not_a_second_opening(sms):
    first = _new_lead(35)
    assert _state(first) == LeadState.CONTACTED
    sms.outbox.clear()
    second = _new_lead(36)  # same seller, different car
    assert sms.outbox == [] and _state(second) == LeadState.NEW
    with session_scope() as s:
        reasons = [e.reason for e in s.scalars(select(Escalation).where(Escalation.lead_id == second))]
    assert reasons == ["sms_number_in_use"]
