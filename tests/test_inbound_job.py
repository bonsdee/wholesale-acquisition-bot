"""Inbound messages are handled by the worker, and a retried job never records the seller twice."""

from datetime import UTC, datetime

from sqlalchemy import select

from acqbot.db import session_scope
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Channel, Job, JobStatus, Lead, LeadState, Message
from acqbot.queue.jobs import enqueue
from acqbot.queue.worker import drain
from acqbot.simulator import make_lead
from acqbot.transport.protocol import InboundMessage, TransportError
from acqbot.transport.registry import reset_console_transport


def test_retried_inbound_job_is_idempotent(monkeypatch):
    console = reset_console_transport()
    monkeypatch.setattr("acqbot.transport.registry.get_transport", lambda channel: console)
    with session_scope() as s:
        lead_id = ingest_lead(s, make_lead("clean", seed=61)).lead_id
    drain("test")  # enrichment

    real_send = console.send
    failures = {"left": 1}

    def flaky_send(thread, body):
        if failures["left"]:
            failures["left"] -= 1
            raise TransportError("channel hiccup", retryable=True)
        return real_send(thread, body)

    monkeypatch.setattr(console, "send", flaky_send)

    msg = InboundMessage(
        channel=Channel.CONSOLE,
        external_id="seller-61",
        body="Hi, saw you're keen on my car",
        external_msg_id="ext-1",
        referral_ref=str(lead_id),
        received_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
    )
    assert InboundMessage.from_payload(msg.to_payload()) == msg
    with session_scope() as s:
        enqueue(s, "inbound_message", msg.to_payload(), dedupe_key="inbound:console:ext-1", max_attempts=3)

    drain("test")  # first attempt: the opening fails to send → job scheduled for retry
    with session_scope() as s:
        job = s.scalars(select(Job).where(Job.kind == "inbound_message")).one()
        assert job.status == JobStatus.FAILED and job.attempts == 1
        job.run_at = datetime.now(UTC)  # skip the backoff
        assert s.get(Lead, lead_id).state == LeadState.NEW

    drain("test")  # second attempt succeeds
    with session_scope() as s:
        job = s.scalars(select(Job).where(Job.kind == "inbound_message")).one()
        assert job.status == JobStatus.DONE
        msgs = list(s.scalars(select(Message).where(Message.lead_id == lead_id).order_by(Message.sent_at)))
        assert [m.direction.value for m in msgs] == ["inbound", "outbound"]
        assert s.get(Lead, lead_id).state == LeadState.CONTACTED
    assert "automated assistant" in console.last_sent("seller-61")
